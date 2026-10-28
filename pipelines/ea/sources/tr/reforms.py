import re
import subprocess
from pathlib import Path
from typing import LiteralString
from urllib.parse import quote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr import ysk_candidates_2009, ysk_mayors
from ea.sources.tr.ysk import PROVINCES, upper
from ea.sources.tr.ysk_local import title
from ea.store import DATA, connect

SOURCE = "tr.reforms"
VERSION = "1"
LICENSE = "Laws and official texts may be freely reproduced (Law 5846 art. 31)."
PARSED = DATA / "parsed" / SOURCE
ICISLERI = "https://icisleri.gov.tr/kurumlar/icisleri.gov.tr/IcSite/illeridaresi/" + quote(
    "İstatistiki Bilgiler/İl İdaresi ve Mülki Bölümler/il ilce kurulus tarihleri_2019.pdf"
)
LAWS = {
    6360: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.6360.pdf",
    5747: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.5747.pdf",
    3392: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3392.pdf",
    3644: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3644.pdf",
    3647: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3647.pdf",
    3030: "https://www5.tbmm.gov.tr/tutanaklar/KANUNLAR_KARARLAR/kanuntbmmc067/kanuntbmmc067/kanuntbmmc06703030.pdf",
    5216: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.5216.pdf",
    3306: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3306.pdf",
    3391: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3391.pdf",
    3398: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3398.pdf",
    3399: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3399.pdf",
    3508: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3508.pdf",
    504: "https://www.mevzuat.gov.tr/MevzuatMetin/4.5.504.pdf",
    593: "https://www.mevzuat.gov.tr/MevzuatMetin/4.5.593.pdf",
    3578: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3578.pdf",
    3806: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3806.pdf",
    550: "https://www.mevzuat.gov.tr/MevzuatMetin/4.5.550.pdf",
    584: "https://www.mevzuat.gov.tr/MevzuatMetin/4.5.584.pdf",
    3760: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3760.pdf",
    4200: "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.4200.pdf",
}
GAZETTE_6447 = "https://www.resmigazete.gov.tr/eskiler/2013/03/20130322-2.htm"
ORIGINALS = {
    "5216": "https://www.resmigazete.gov.tr/eskiler/2004/07/20040723.htm",
    "5747": "https://www.resmigazete.gov.tr/eskiler/2008/03/20080322M1-1.htm",
    "6360": "https://www.resmigazete.gov.tr/eskiler/2012/12/20121206-1.htm",
    "6447": GAZETTE_6447,
}
POPULATION = (
    "https://dergipark.org.tr/en/download/article-file/1026739",
    (
        "Ozan Yetkin, Türkiye'de Büyükşehir Belediyelerinin Yapısı ve Geleceği, Akademik Düşünce Dergisi 1 (2020), "
        "Tablo 1, from DİE's 2000 General Population Census"
    ),
)
CENTERS = {
    "06": (39.92645, 32.85616, 3_203_362, "OpenStreetMap way 1062345960, Ankara Valiliği"),
    "35": (38.41875, 27.12970, 2_232_265, "OpenStreetMap node 8086003630, İzmir İli Hükümet Konağı"),
    "01": (37.00034, 35.31335, 1_130_710, "OpenStreetMap way 441966319, Adana Valiliği"),
    "16": (40.18269, 29.06615, 1_194_687, "OpenStreetMap way 254569407, Bursa Valiliği"),
    "27": (37.06567, 37.37574, 853_513, "OpenStreetMap way 237059449, Gaziantep Valiliği"),
    "42": (37.87150, 32.49897, 742_690, "OpenStreetMap relation 7585286, Konya Valilik"),
    "38": (38.72289, 35.48911, 536_392, "OpenStreetMap way 255192353, Kayseri Valiliği"),
    "07": (36.88810, 30.70264, 603_190, "OpenStreetMap way 431455136, Antalya Valiliği"),
    "21": (37.91588, 40.22522, 545_983, "OpenStreetMap node 4592219790, Diyarbakır Valiliği"),
    "25": (39.90001, 41.26455, 361_235, "OpenStreetMap node 14206239502, Erzurum Valiliği"),
    "26": (39.77066, 30.51782, 482_793, "OpenStreetMap way 308893739, Eskişehir Valiliği"),
    "33": (
        36.79900,
        34.63200,
        537_842,
        (
            "Uray Caddesi in central Mersin, the seat of government before the move to Akkent; the building used in "
            "2004 is not located, so the center may be off by about a kilometer"
        ),
    ),
    "55": (41.28971, 36.33824, 363_180, "OpenStreetMap way 88116142, Samsun Valiliği"),
    "54": (
        40.77900,
        30.40100,
        303_989,
        (
            "the site of the Sakarya Hükümet Konağı on Çark Caddesi, demolished after the 1999 earthquake; the "
            "governorship's temporary building in 2004 is not located, so the center may be off by about a kilometer"
        ),
    ),
}
GUIDE_6360 = "https://www.tbb.gov.tr/sites/default/files/storage/userfiles/rehber_ve_bilgi_notlari/6360_torba_personel_rehber.pdf"
SPELLINGS = {"AFYON": "AFYONKARAHİSAR"}
ROW = re.compile(
    r"^\s*(?:(\d+)\s+)?([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ .'()-]*?)\s{2,}(?:(\d\d\.\d\d\.\d{4})|CUMHURİYET ÖNCESİ)"
    r"(?:\s+(\S+(?: \S+)*?))?\s*(\d\d\.\d\d\.\d{4}-\d+)?\s*$"
)

ELEVATED = {
    "64": ["Kütahya"],
    "02": ["Malatya"],
    "54": ["Kocaeli"],
    "71": ["Ankara"],
    "68": ["Niğde"],
    "69": ["Gümüşhane"],
    "70": ["Konya"],
    "72": ["Siirt"],
    "73": ["Siirt"],
    "74": ["Zonguldak"],
    "75": ["Kars"],
    "76": ["Kars"],
    "78": ["Zonguldak"],
    "79": ["Gaziantep"],
    "77": ["İstanbul"],
    "80": ["Adana"],
    "81": ["Bolu"],
}
METROPOLISES = [
    ("34", "İstanbul", "1984-03-08", "KHK 195; 3030", "09.07.1984-18453"),
    ("06", "Ankara", "1984-03-08", "KHK 195; 3030", "09.07.1984-18453"),
    ("35", "İzmir", "1984-03-08", "KHK 195; 3030", "09.07.1984-18453"),
    ("01", "Adana", "1986-06-19", "3306", "19.06.1986-19139"),
    ("16", "Bursa", "1987-06-27", "3391", "27.06.1987-19500"),
    ("27", "Gaziantep", "1987-06-27", "3398", "27.06.1987-19500"),
    ("42", "Konya", "1987-06-27", "3399", "27.06.1987-19500"),
    ("38", "Kayseri", "1988-12-14", "3508", "14.12.1988-20019"),
    *(
        (plate, name, "1993-09-09", "KHK 504", "09.09.1993-21693")
        for plate, name in (
            ("07", "Antalya"),
            ("21", "Diyarbakır"),
            ("25", "Erzurum"),
            ("26", "Eskişehir"),
            ("41", "İzmit"),
            ("33", "Mersin"),
            ("55", "Samsun"),
        )
    ),
    ("54", "Adapazarı", "2000-03-06", "KHK 593", "06.03.2000-23985"),
    *(
        (plate, name, "2014-03-30", "6360", "06.12.2012-28489")
        for plate, name in (
            ("09", "Aydın"),
            ("10", "Balıkesir"),
            ("20", "Denizli"),
            ("31", "Hatay"),
            ("44", "Malatya"),
            ("45", "Manisa"),
            ("46", "Kahramanmaraş"),
            ("47", "Mardin"),
            ("48", "Muğla"),
            ("59", "Tekirdağ"),
            ("61", "Trabzon"),
            ("63", "Şanlıurfa"),
            ("65", "Van"),
        )
    ),
    ("52", "Ordu", "2014-03-30", "6447", "22.03.2013-28595"),
]
PROVINCE_RULE_6360 = [plate for plate, *_ in METROPOLISES if plate not in ("34", "41")]
RADIUS = (
    "Law 5216 provisional art. 2(1) as enacted in 2004: a circle around the valilik building, 20 km up to 1 million "
    "people, 30 km to 2 million, 50 km above, inside the province. Its second paragraph made the ilçes inside the "
    "circle metropolitan ilçe municipalities and the beldes first-tier municipalities; Law 5747 art. 3 repealed it "
    "in 2008. Its fourth paragraph let municipalities and villages of those ilçes outside the circle join within six "
    "months by council decision, which this boundary does not show."
)
VILLAGES = (
    "Law 5216 provisional art. 2(3) as enacted in 2004: every köy inside the circle lost its legal personality and "
    "became a mahalle, forest villages excepted; the metropolitan council assigned each to an ilçe or first-tier "
    "municipality."
)
BOUNDARY_RULES = [
    *(
        (plate, name, "2004-07-23", "5216", "23.07.2004-25531", "province", "Law 5216 provisional art. 2")
        for plate, name in (("34", "İstanbul"), ("41", "İzmit"))
    ),
    *(
        (
            plate,
            name,
            "2004-07-23",
            "5216",
            "23.07.2004-25531",
            "radius",
            RADIUS,
        )
        for plate, name, day, *_ in METROPOLISES
        if day < "2004-07-23" and plate not in ("34", "41")
    ),
    *(
        (plate, name, "2014-03-30", "6360", "06.12.2012-28489", "province", "Law 6360 art. 1(1) and 1(2)")
        for plate, name, *_ in METROPOLISES
        if plate not in ("34", "41")
    ),
]
CLOSED_5747 = (
    "Law 5747 provisional art. 1(1) and annex (44): the belde municipality became a köy at the March 29, 2009 "
    "local elections. The Constitutional Court (E. 2008/34, K. 2008/153) annulled this for beldes that sued over "
    "the census, passed 2,000 people by merger before March 22, 2008, or lay in tourism areas."
)
KEPT_5747 = (
    "Listed in Law 5747 annex (44) for conversion to a köy, but the belde still elected a mayor on March 29, 2009 "
    "(YSK's candidate list), so the Constitutional Court's annulment kept it a municipality."
)
BELDE_6360 = (
    "Law 6360 art. 1(3): the belde municipality's legal personality ended at the March 30, 2014 local elections and "
    "it joined its ilçe municipality as one mahalle under its own name. The list is every belde in the province that "
    "elected a mayor in 2009."
)
SMALL_6360 = (
    "Law 6360 provisional art. 2(1) and annex (27): a belde municipality under 2,000 people in the 2011 census "
    "became a köy at the March 30, 2014 local elections."
)
SPLIT_6360 = "Law 6360 art. 2, in force at the March 30, 2014 local elections"
ILCE_CHANGES = [
    ("1986-06-19", "3306", "19.06.1986-19139", "renamed", "01", "Seyhan", ["Merkez"], [], "Law 3306 art. 1"),
    ("1986-06-19", "3306", "19.06.1986-19139", "split", "01", "Seyhan", [], ["Seyhan", "Yüreğir"], "Law 3306 art. 1"),
    (
        "1987-06-27",
        "3391",
        "27.06.1987-19500",
        "split",
        "16",
        "Merkez",
        [],
        ["Osmangazi", "Yıldırım", "Nilüfer"],
        "Law 3391 art. 1",
    ),
    (
        "1987-06-27",
        "3398",
        "27.06.1987-19500",
        "split",
        "27",
        "Merkez",
        [],
        ["Şehitkamil", "Şahinbey"],
        "Law 3398 art. 1",
    ),
    (
        "1987-06-27",
        "3399",
        "27.06.1987-19500",
        "split",
        "42",
        "Merkez",
        [],
        ["Karatay", "Selçuklu", "Meram"],
        "Law 3399 art. 1",
    ),
    (
        "1988-12-14",
        "3508",
        "14.12.1988-20019",
        "split",
        "38",
        "Merkez",
        [],
        ["Melikgazi", "Kocasinan"],
        "Law 3508 art. 1",
    ),
    (
        "1983-11-30",
        "2963",
        "30.11.1983-18237",
        "split",
        "06",
        "Merkez",
        [],
        ["Gölbaşı", "Keçiören", "Mamak", "Sincan"],
        "Law 2963 art. 1",
    ),
    ("1987-07-04", "3392", "04.07.1987-19507", "split", "35", "Merkez", [], ["Konak", "Buca"], "Law 3392 art. 1(55)"),
    *(
        ("2008-03-22", "5747", "22.03.2008-26824", "split", plate, "Merkez", [], targets, "Law 5747 art. 1")
        for plate, targets in (
            ("07", ["Muratpaşa", "Kepez", "Konyaaltı", "Aksu", "Döşemealtı"]),
            ("21", ["Bağlar", "Kayapınar", "Sur", "Yenişehir"]),
            ("25", ["Yakutiye", "Palandöken"]),
            ("26", ["Odunpazarı", "Tepebaşı"]),
            ("33", ["Akdeniz", "Mezitli", "Toroslar", "Yenişehir"]),
            ("41", ["İzmit", "Başiskele", "Kartepe"]),
            ("54", ["Adapazarı", "Serdivan", "Arifiye", "Erenler"]),
            ("55", ["İlkadım", "Canik", "Atakum"]),
        )
    ),
    ("2008-03-22", "5747", "22.03.2008-26824", "absorbed", "34", "Fatih", ["Eminönü"], [], "Law 5747 art. 2(2)"),
    ("2008-03-22", "5747", "22.03.2008-26824", "renamed", "25", "Aziziye", ["Ilıca"], [], "Law 5747 art. 2(5)"),
    *(
        ("2014-03-30", "6360", "06.12.2012-28489", "renamed", plate, new, ["Merkez"], [], SPLIT_6360)
        for plate, new in (
            ("09", "Efeler"),
            ("47", "Artuklu"),
            ("48", "Menteşe"),
            ("52", "Altınordu"),
            ("59", "Süleymanpaşa"),
            ("61", "Ortahisar"),
        )
    ),
    ("2014-03-30", "6360", "06.12.2012-28489", "renamed", "20", "Pamukkale", ["Akköy"], [], SPLIT_6360),
    *(
        ("2014-03-30", "6360", "06.12.2012-28489", "split", plate, "Merkez", [], targets, SPLIT_6360)
        for plate, targets in (
            ("10", ["Karesi", "Altıeylül"]),
            ("20", ["Merkezefendi", "Pamukkale"]),
            ("31", ["Antakya", "Defne", "Altınözü", "Yayladağı"]),
            ("44", ["Yeşilyurt", "Battalgazi"]),
            ("45", ["Şehzadeler", "Yunusemre", "Köprübaşı"]),
            ("46", ["Dulkadiroğlu", "Onikişubat"]),
            ("63", ["Eyyübiye", "Haliliye", "Karaköprü"]),
            ("65", ["Tuşba", "İpekyolu", "Edremit"]),
        )
    ),
    *(
        ("2014-03-30", "6360", "06.12.2012-28489", "split", plate, source, [], [source, *created], SPLIT_6360)
        for plate, source, created in (
            ("31", "İskenderun", ["Arsuz"]),
            ("31", "Dörtyol", ["Payas"]),
            ("48", "Fethiye", ["Seydikemer"]),
            ("59", "Çerkezköy", ["Kapaklı"]),
            ("59", "Çorlu", ["Ergene"]),
            ("67", "Merkez", ["Kilimli", "Kozlu"]),
        )
    ),
]


def fetch() -> None:
    raw.fetch(SOURCE, ICISLERI, suffix=".pdf")
    for link in LAWS.values():
        raw.fetch(SOURCE, link, suffix=".pdf")
    raw.fetch(SOURCE, GUIDE_6360, suffix=".pdf")
    raw.fetch(SOURCE, POPULATION[0], suffix=".pdf")
    for link in ORIGINALS.values():
        raw.fetch(SOURCE, link, suffix=".htm")


def text(link: str) -> str:
    path = raw.cached(SOURCE, link).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def event(
    day: str,
    law: str,
    gazette: str,
    kind: str,
    scheme: str,
    plate: str,
    name: str,
    *,
    parent: str | None = None,
    sources: list[str] | None = None,
    targets: list[str] | None = None,
    rule: str | None = None,
    note: str | None = None,
) -> dict[str, object]:
    return {
        "date": day,
        "law": law,
        "gazette": gazette,
        "kind": kind,
        "scheme": scheme,
        "plate": plate,
        "name": name,
        "parent": parent,
        "sources": sources or [],
        "targets": targets or [],
        "rule": rule,
        "note": note,
    }


def parse() -> None:
    plates = {upper(name): f"{plate:02}" for plate, name in enumerate(PROVINCES, 1)}
    founded: list[dict[str, object]] = []
    province = ""
    previous = ""
    for line in text(ICISLERI).splitlines():
        if heading := re.match(r"^\s*\*(.+?)\*?\s*$", line):
            province = heading.group(1)
        elif match := ROW.match(line):
            law = match.group(4)
            if law is not None and not law.isdigit():
                decree = re.search(r"(\d+) SAYILI", previous)
                law = None if decree is None else f"KHK {decree.group(1)}"
            founded.append(
                {
                    "plate": plates[SPELLINGS.get(province, province)],
                    "province": province,
                    "name": match.group(2).strip(),
                    "is_province_seat": match.group(1) is None,
                    "founded": match.group(3),
                    "law": law,
                    "gazette": match.group(5),
                }
            )
        if line.strip():
            previous = line
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(founded).write_parquet(PARSED / "ilce_2019.parquet")

    events: list[dict[str, object]] = []
    for row in founded:
        if row["founded"] is not None:
            day, month, year = str(row["founded"]).split(".")
            events.append(
                event(
                    f"{year}-{month}-{day}",
                    str(row["law"]),
                    str(row["gazette"]),
                    "created",
                    "tr.il" if row["is_province_seat"] else "tr.ilce",
                    str(row["plate"]),
                    str(row["name"]),
                    sources=ELEVATED.get(str(row["plate"]), []) if row["is_province_seat"] else [],
                    note="İçişleri Bakanlığı, İl ve İlçe Kuruluş Tarihleri (2019)",
                )
            )
    for plate, name, day, law, gazette in METROPOLISES:
        events.append(event(day, law, gazette, "created", "tr.buyuksehir", plate, name, rule="municipal"))
    for plate, name, day, law, gazette, rule, note in BOUNDARY_RULES:
        events.append(event(day, law, gazette, "boundary_changed", "tr.buyuksehir", plate, name, rule=rule, note=note))
        if rule == "radius":
            events.append(
                event(day, law, gazette, "villages_converted", "tr.buyuksehir", plate, name, rule=rule, note=VILLAGES)
            )
    for plate, old, new in (("41", "İzmit", "Kocaeli"), ("54", "Adapazarı", "Sakarya")):
        events.append(
            event("2008-03-22", "5747", "22.03.2008-26824", "renamed", "tr.buyuksehir", plate, new, sources=[old])
        )
    for plate in PROVINCE_RULE_6360 + ["34", "41"]:
        events.append(
            event(
                "2014-03-30",
                "6360",
                "06.12.2012-28489",
                "council_abolished",
                "tr.il",
                plate,
                PROVINCES[int(plate) - 1],
                note="Law 6360 art. 1(5): the il özel idaresi's legal personality ends; no provincial council elected",
            )
        )
    for day, law, gazette, kind, plate, name, sources, targets, note in ILCE_CHANGES:
        events.append(
            event(day, law, gazette, kind, "tr.ilce", plate, name, sources=sources, targets=targets, note=note)
        )

    law_5747 = text(LAWS[5747])
    article = law_5747[law_5747.index("MADDE 1 –") : law_5747.index("adlarıyla kırküç ilçe kurulmuştur")]
    for item in re.split(r"\n\s*\d+\. ", "\n" + article.split("\n", 1)[1])[1:]:
        flat = " ".join(item.split())
        target = re.search(r"([A-ZÇĞİÖŞÜ][a-zçğıöşü]+) İlinde ([A-ZÇĞİÖŞÜ][a-zçğıöşü]+),?$", flat)
        if target is None:
            raise ValueError(f"5747 art. 1: no ilçe in {flat[-80:]}")
        plate = plates[upper(target.group(1))]
        abolished = re.search(r"^(.*?) ilk kademe belediyelerinin", flat) or re.search(
            r"^(\S+) İlk Kademe Belediyesinin tüzel kişiliği kaldırılarak", flat
        )
        if abolished is None:
            continue
        joined = re.search(r"birlikte (\S+) İlk Kademe Belediyesine katılmıştır", flat)
        for town in re.split(r", | ve ", abolished.group(1)):
            events.append(
                event(
                    "2009-03-29",
                    "5747",
                    "22.03.2008-26824",
                    "absorbed",
                    "tr.belde",
                    plate,
                    town,
                    targets=[joined.group(1) if joined else target.group(2)],
                    parent=target.group(2),
                    note="Law 5747 art. 1: ilk kademe belediyesi abolished at the March 29, 2009 local elections",
                )
            )
    annex = law_5747[law_5747.index("(42) SAYILI LİSTE") : law_5747.index("(43) SAYILI LİSTE")]
    city = ""
    for line in annex.splitlines():
        if re.match(r"^[A-ZÇĞİÖŞÜ]{3,}\s*$", line):
            city = line.strip()
        elif match := re.match(r"^\s*\d+- (\S.*?)\s{2,}(\S.*?)\s*$", line):
            events.append(
                event(
                    "2009-03-29",
                    "5747",
                    "22.03.2008-26824",
                    "absorbed",
                    "tr.belde",
                    plates[city],
                    match.group(1),
                    targets=[match.group(2)],
                    parent=match.group(2),
                    note="Law 5747 art. 2(1) and annex (42): ilk kademe belediyesi joined its ilçe municipality",
                )
            )
    candidates = pl.read_parquet(ysk_candidates_2009.PARSED / "candidates.parquet").filter(pl.col("kind") == "belde")
    towns_2009 = {
        (plates[str(province)], str(district), str(town))
        for province, district, town in candidates.select("province", "district", "town").unique().iter_rows()
    }
    held_2009 = {(plate, town) for plate, _, town in towns_2009}
    annex = law_5747[law_5747.index("(44) SAYILI LİSTE") : law_5747.index("(45) SAYILI LİSTE")]
    for match in re.finditer(r"^\s*\d+\.\s+(\S+(?: \S+)?)\s{2,}(\S+(?: \S+)?)\s{2,}(\S.*?)\s*$", annex, re.MULTILINE):
        events.append(
            event(
                "2009-03-29",
                "5747",
                "22.03.2008-26824",
                "dissolved",
                "tr.belde",
                plate := plates[SPELLINGS.get(match.group(1), match.group(1))],
                match.group(3),
                parent=match.group(2),
                note=CLOSED_5747 if (plate, match.group(3)) not in held_2009 else KEPT_5747,
            )
            | ({} if (plate, match.group(3)) not in held_2009 else {"kind": "kept"})
        )
    for plate, district, town in sorted(towns_2009):
        if plate in PROVINCE_RULE_6360:
            events.append(
                event(
                    "2014-03-30",
                    "6360",
                    "06.12.2012-28489",
                    "absorbed",
                    "tr.belde",
                    plate,
                    town,
                    parent=district,
                    targets=[district],
                    note=BELDE_6360,
                )
            )
    law_6360 = text(LAWS[6360])
    annex = law_6360[law_6360.index("(27) SAYILI LİSTE") : law_6360.index("6360 SAYILI KANUNA EK")]
    for match in re.finditer(r"^\s*\d+\s+(\S+)\s{2,}(\S+(?: \S+)?)\s{2,}(\S.*?)\s*$", annex, re.MULTILINE):
        province, district, town = (upper(part) for part in match.groups())
        if plates[SPELLINGS.get(province, province)] in PROVINCE_RULE_6360:
            continue
        events.append(
            event(
                "2014-03-30",
                "6360",
                "06.12.2012-28489",
                "dissolved",
                "tr.belde",
                plates[SPELLINGS.get(province, province)],
                town,
                parent=district,
                note=SMALL_6360,
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(events, infer_schema_length=None).write_parquet(PARSED / "events.parquet")


def emit() -> Path:
    events = pl.read_parquet(PARSED / "events.parquet")
    founded = pl.read_parquet(PARSED / "ilce_2019.parquet")
    plates = {upper(name): f"{plate:02}" for plate, name in enumerate(PROVINCES, 1)}
    aliases = {"EYÜP": "EYÜPSULTAN", "BAHŞİLİ": "BAHŞILI", "ONDOKUZMAYIS": "19-MAY"}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Turkey's administrative reforms: ilçe and province creations, splits, renames, and closures; "
                "metropolitan municipalities with their dated boundaries; belde municipalities as dated areas tied to "
                "their polities, with the 5747 and 6360 closures; and the provincial councils 6360 abolished"
            ),
            polity="TR",
        )

        def name(entity: UUID, text: str, lang: str = "tr", kind: str = "official", **valid: object) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official",
                valid_from=valid.get("valid_from"),
                valid_from_precision="day",
                valid_to=valid.get("valid_to"),
                valid_to_precision="day",
            )

        def source(
            publisher: str, heading: str, url: str | None, kind: str = "official_body", **extra: str | None
        ) -> UUID:
            id = cs.source(publisher, heading)
            if id in cs.minted:
                cs.add(
                    "source",
                    id=id,
                    kind=kind,
                    publisher=publisher,
                    title=heading,
                    url=url,
                    license=extra.get("license", LICENSE),
                    license_url=None,
                    attribution=publisher,
                    notes=extra.get("notes"),
                )
            return id

        def event(kind: str, day: str, source_id: UUID, notes: str | None, members: list[tuple[UUID, str]]) -> None:
            id = cs.key(f"{SOURCE}.event", f"{kind}:{day}:{':'.join(f'{role}={area}' for area, role in members)}")
            cs.add("area_event", id=id, kind=kind, date=day, date_precision="day", source_id=source_id, notes=notes)
            for area, role in dict.fromkeys(members):
                cs.add("area_event_member", event_id=id, area_id=area, role=role)

        turkey = cs.polity("TR")
        interior = source("T.C. İçişleri Bakanlığı", "İl ve İlçe Kuruluş Tarihleri (2019)", ICISLERI)
        cs.artifact(raw.cached(SOURCE, ICISLERI), interior)
        laws: dict[str, UUID] = {}
        for law, gazette in events.select("law", "gazette").unique().rows():
            if law == "None":
                laws[law] = interior
                continue
            numbers = [part.removeprefix("KHK ") for part in law.split("; ")]
            words = " and ".join(
                f"Decree-Law No. {part[4:]}" if part.startswith("KHK") else f"Law No. {part}"
                for part in law.split("; ")
            )
            day, issue = gazette.split("-") if gazette != "None" else ("", "")
            heading = f"{words} (Resmî Gazete {issue}, {day})" if issue else words
            links = [LAWS[int(number)] for number in numbers if int(number) in LAWS]
            laws[law] = source(
                "Resmî Gazete",
                heading,
                links[0] if links else ORIGINALS.get(numbers[-1]),
                notes="Consolidated text from the Mevzuat Bilgi Sistemi (mevzuat.gov.tr)" if links else None,
            )
            for link in [*links, *([ORIGINALS[numbers[-1]]] if numbers[-1] in ORIGINALS else [])]:
                cs.artifact(raw.cached(SOURCE, link), laws[law])
        guide = source("Türkiye Belediyeler Birliği", "6360 Sayılı Kanun Personel Rehberi", GUIDE_6360)
        cs.artifact(raw.cached(SOURCE, GUIDE_6360), guide)
        population = source(
            "Akademik Düşünce Dergisi", POPULATION[1], POPULATION[0], kind="academic", license="No license stated."
        )
        cs.artifact(raw.cached(SOURCE, POPULATION[0]), population)

        provinces = {plate: cs.area("tr.il", plate) for plate in plates.values()}
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
        polities = {
            value: id
            for value, id in conn.execute("select value, entity_id from external_id where scheme = 'tr.polity'")
        }

        def find(plate: str, text: str) -> UUID | None:
            label = aliases.get(upper(text), upper(text))
            if label in ("MERKEZ", upper(PROVINCES[int(plate) - 1])):
                label = f"{upper(PROVINCES[int(plate) - 1])} MERKEZ"
            return districts.get((plate, label)) or districts.get((plate, upper(text)))

        def geometry(query: LiteralString, params: list[object]) -> tuple[str, str, str, UUID] | None:
            return conn.execute(query, params).fetchone()

        historical: dict[tuple[str, str], UUID] = {}

        def former(plate: str, text: str, day: str, law: str, successors: list[UUID]) -> UUID:
            label = f"{PROVINCES[int(plate) - 1]} Merkez" if text == "Merkez" else text
            area = cs.key(f"{SOURCE}.ilce", f"{plate}:{label}:{day}")
            if (plate, label) not in historical:
                historical[(plate, label)] = area
                cs.add("area", id=area, scheme_code="tr.ilce")
                name(area, label, valid_to=day)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=provinces[plate],
                    valid_from=None,
                    valid_from_precision="day",
                    valid_to=day,
                    valid_to_precision="day",
                )
                shape = geometry(
                    """select encode(st_asewkb(st_multi(st_union(geom))), 'hex'),
                              encode(st_asewkb(st_pointonsurface(st_union(geom))), 'hex'), null, null
                       from area_geometry where area_id = any(%s) and upper_inf(valid)""",
                    [successors],
                )
                if successors and shape is not None and shape[0] is not None:
                    cs.add(
                        "area_geometry",
                        area_id=area,
                        valid_from=None,
                        valid_from_precision="day",
                        valid_to=day,
                        valid_to_precision="day",
                        geom=shape[0],
                        label_point=shape[1],
                        quality="synthetic",
                        source_id=laws[law],
                    )
            return historical[(plate, label)]

        counts: dict[str, int] = {}
        missing: list[str] = []
        for row in events.filter(pl.col("scheme") == "tr.ilce").iter_rows(named=True):
            day, plate, kind = row["date"], row["plate"], row["kind"]
            area = find(plate, row["name"])
            targets = [find(plate, target) for target in row["targets"]]
            successors = [target for target in targets if target is not None]
            if len(successors) < len(targets) or (area is None and kind != "split"):
                missing.append(f"{kind} {plate} {row['name']} {row['targets']}")
                continue
            if kind == "split":
                members = [(area or former(plate, row["name"], day, row["law"], successors), "from")]
                members += [(target, "to") for target in successors]
            elif area is None:
                continue
            elif kind == "created":
                members = [(area, "to")]
            elif kind == "renamed":
                members = [(area, "from"), (area, "to")]
                old = row["sources"][0]
                name(
                    area,
                    f"{PROVINCES[int(plate) - 1]} Merkez" if old == "Merkez" else old,
                    kind="historical",
                    valid_to=day,
                )
            else:
                members = [*((former(plate, s, day, row["law"], []), "from") for s in row["sources"]), (area, "to")]
            notes = row["note"] if kind != "renamed" else f"{row['sources'][0]} renamed {row['name']}. {row['note']}"
            event(kind, day, interior if kind == "created" else laws[row["law"]], notes, members)
            counts[f"tr.ilce {kind}"] = counts.get(f"tr.ilce {kind}", 0) + 1

        for row in events.filter(pl.col("scheme") == "tr.il").iter_rows(named=True):
            day, plate = row["date"], row["plate"]
            if row["kind"] == "created":
                older = [
                    find(plate, ilce)
                    for ilce, start in founded.filter((pl.col("plate") == plate) & ~pl.col("is_province_seat"))
                    .select("name", "founded")
                    .rows()
                    if start is None or "-".join(reversed(start.split("."))) < day
                ]
                members = [*((a, "from") for a in [find(plate, "Merkez"), *older] if a is not None)]
                notes = (
                    f"{PROVINCES[int(plate) - 1]} became a province on {day}"
                    + (f", formed from ilçes of {' and '.join(row['sources'])}" if row["sources"] else "")
                    + ". The ilçes are those İçişleri dates before the province, matched to today's ilçes."
                )
                event("created", day, interior, notes, [*members, (provinces[plate], "to")])
                counts["tr.il created"] = counts.get("tr.il created", 0) + 1
            else:
                province = PROVINCES[int(plate) - 1]
                key = f"{slug(province)}-il-ozel-idaresi"
                administration = cs.key("tr.polity", key)
                if administration in cs.minted:
                    cs.add("polity", id=administration, kind="region", iso_code=None)
                    name(administration, f"{province} Special Provincial Administration", "en")
                    name(administration, f"{province} İl Özel İdaresi", kind="native")
                    cs.add("polity_parent", polity_id=administration, parent_id=turkey)
                    cs.add("slug", path=f"tr/{slug(province)}/il-ozel-idaresi", entity_id=administration)
                    cs.add(
                        "polity_territory",
                        polity_id=administration,
                        area_id=provinces[plate],
                        valid_from=None,
                        valid_from_precision="day",
                        valid_to=day,
                        valid_to_precision="day",
                    )
                council = cs.key("tr.body", f"{key}:il-genel-meclisi")
                if council in cs.minted:
                    cs.add("body", id=council, polity_id=administration, kind="council")
                    name(council, f"{province} Provincial Council", "en")
                    name(council, f"{province} İl Genel Meclisi", kind="native")
                cs.add(
                    "body_event",
                    id=cs.key(f"{SOURCE}.body_event", f"{key}:{day}"),
                    body_id=council,
                    kind="dissolved",
                    date=day,
                    date_precision="day",
                    source_id=laws[row["law"]],
                )
                counts["tr.il council dissolved"] = counts.get("tr.il council dissolved", 0) + 1

        claimed: dict[UUID, str | None] = {
            polity: None if start is None else str(start)
            for polity, start in conn.execute(
                """select pt.polity_id, min(lower(pt.valid)) from polity_territory pt
                   join changeset c on c.id = pt.changeset_id
                   where c.pipeline <> %s group by pt.polity_id having bool_and(not lower_inf(pt.valid))""",
                [SOURCE],
            )
        } | {
            polity: ""
            for (polity,) in conn.execute(
                """select pt.polity_id from polity_territory pt join changeset c on c.id = pt.changeset_id
                   where c.pipeline <> %s and lower_inf(pt.valid)""",
                [SOURCE],
            )
        }
        metros: dict[str, UUID] = {}
        for row in events.filter(pl.col("scheme") == "tr.buyuksehir").sort("date").iter_rows(named=True):
            day, plate, kind = row["date"], row["plate"], row["kind"]
            area = metros.setdefault(plate, cs.key("tr.buyuksehir", plate))
            if kind == "created":
                cs.add("area", id=area, scheme_code="tr.buyuksehir")
                renamed = events.filter(
                    (pl.col("scheme") == "tr.buyuksehir") & (pl.col("plate") == plate) & (pl.col("kind") == "renamed")
                ).rows(named=True)
                until = renamed[0]["date"] if renamed else None
                name(area, f"{row['name']} Büyükşehir Belediyesi", valid_from=day, valid_to=until)
                name(area, f"{row['name']} Metropolitan Municipality", "en", "english", valid_from=day, valid_to=until)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=provinces[plate],
                    valid_from=day,
                    valid_from_precision="day",
                    valid_to=None,
                    valid_to_precision="day",
                )
                polity = polities.get(slug(PROVINCES[int(plate) - 1]))
                held = None if polity is None else claimed.get(polity)
                if polity is not None and (held is None or held > day):
                    cs.add(
                        "polity_territory",
                        polity_id=polity,
                        area_id=area,
                        valid_from=day,
                        valid_from_precision="day",
                        valid_to=held,
                        valid_to_precision="day",
                    )
                event(
                    "created",
                    day,
                    laws[row["law"]],
                    f"{row['name']} became a metropolitan municipality.",
                    [(area, "to")],
                )
            elif kind == "renamed":
                name(area, f"{row['name']} Büyükşehir Belediyesi", valid_from=day)
                name(area, f"{row['name']} Metropolitan Municipality", "en", "english", valid_from=day)
                event(
                    "renamed",
                    day,
                    laws[row["law"]],
                    f"{row['sources'][0]} renamed {row['name']}.",
                    [(area, "from"), (area, "to")],
                )
            elif kind == "boundary_changed":
                notes = str(row["note"])
                if row["rule"] == "radius":
                    lat, lon, people, center = CENTERS[plate]
                    radius = 20 if people <= 1_000_000 else 30 if people <= 2_000_000 else 50
                    shape = geometry(
                        """select encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_intersection(
                                      st_buffer(st_setsrid(st_makepoint(%s, %s), 4326)::geography, %s)::geometry,
                                      g.geom)), 3))), 'hex'),
                                  encode(st_asewkb(st_setsrid(st_makepoint(%s, %s), 4326)), 'hex'), null, null
                           from area_geometry g where g.area_id = %s and g.valid @> %s::date""",
                        [lon, lat, radius * 1000, lon, lat, provinces[plate], day],
                    )
                    until = "2014-03-30"
                    notes = (
                        f"{notes} Drawn as a {radius} km circle for {people:,} people in the metropolitan municipality "
                        f"at the 2000 census ({POPULATION[1]}), centered on {center}, and clipped to the province. "
                        "The shape is approximate."
                    )
                else:
                    shape = geometry(
                        """select encode(st_asewkb(geom), 'hex'), encode(st_asewkb(label_point), 'hex'),
                                  quality::text, source_id
                           from area_geometry where area_id = %s and valid @> %s::date""",
                        [provinces[plate], day],
                    )
                    until = None
                if shape is None:
                    raise ValueError(f"no province shape for {plate} on {day}")
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=day,
                    valid_from_precision="day",
                    valid_to=until,
                    valid_to_precision="day",
                    geom=shape[0],
                    label_point=shape[1],
                    quality=shape[2] or "synthetic",
                    source_id=shape[3] or laws[row["law"]],
                )
                event("boundary_changed", day, laws[row["law"]], notes, [(area, "from"), (area, "to")])
            else:
                counts[f"tr.buyuksehir {kind} (rule only)"] = counts.get(f"tr.buyuksehir {kind} (rule only)", 0) + 1
                continue
            counts[f"tr.buyuksehir {kind}"] = counts.get(f"tr.buyuksehir {kind}", 0) + 1

        towns: dict[str, tuple[str, str, str]] = {}
        district = ""
        for level, province, label in (
            pl.read_parquet(ysk_mayors.PARSED / "mayors_2004.parquet").select("level", "province", "name").rows()
        ):
            plate = plates[ysk_mayors.SPELLINGS.get(upper(province), upper(province))]
            label = re.sub(r"\s*\(\d\)$", "", str(label)).strip()
            if level == "district":
                district = label
            elif level == "town":
                parts = [slug(PROVINCES[int(plate) - 1]), "merkez" if district.startswith("Merkez") else slug(district)]
                towns["-".join([*parts, slug(label)])] = (plate, re.sub(r"\s*\(.*\)$", "", district), label)
        candidates = pl.read_parquet(ysk_candidates_2009.PARSED / "candidates.parquet").filter(
            pl.col("kind") == "belde"
        )
        for province, district, town in candidates.select("province", "district", "town").unique().rows():
            plate = plates[str(province)]
            central = district in (province, "MERKEZ") or district.endswith(" MERKEZ")
            parts = [slug(PROVINCES[int(plate) - 1]), "merkez" if central else slug(district), slug(town)]
            towns.setdefault("-".join(parts), (plate, "Merkez" if central else title(district), title(town)))
        by_town: dict[tuple[str, str], list[str]] = {}
        for key, (plate, _, town) in towns.items():
            by_town.setdefault((plate, slug(town)), []).append(key)

        def town_key(plate: str, parent: str, town: str, register: bool = True) -> str:
            province = PROVINCES[int(plate) - 1]
            central = upper(parent) in ("MERKEZ", upper(province)) or upper(parent).endswith(" MERKEZ")
            key = "-".join([slug(province), "merkez" if central else slug(parent), slug(town)])
            if key not in towns and len(by_town.get((plate, slug(town)), [])) == 1:
                key = by_town[(plate, slug(town))][0]
            if register:
                towns.setdefault(key, (plate, "Merkez" if central else title(parent), title(town)))
            return key

        closures: dict[str, tuple[str, str, list[str], str, str]] = {}
        kept = 0
        for row in events.filter(pl.col("scheme") == "tr.belde").sort("date").iter_rows(named=True):
            key = town_key(row["plate"], str(row["parent"]), row["name"])
            if row["kind"] == "kept":
                kept += 1
            elif key not in closures:
                closures[key] = (row["date"], row["kind"], row["targets"], row["law"], row["note"])
        renamed_centers = {
            plate: new
            for day, _, _, kind, plate, new, sources, _, _ in ILCE_CHANGES
            if kind == "renamed" and sources == ["Merkez"] and day == "2014-03-30"
        }

        def center(plate: str, text: str) -> UUID | None:
            province = upper(PROVINCES[int(plate) - 1])
            if upper(text) not in ("MERKEZ", province, f"{province} MERKEZ"):
                return None
            if plate in renamed_centers:
                return find(plate, renamed_centers[plate])
            return historical.get((plate, f"{PROVINCES[int(plate) - 1]} Merkez"))

        for key, (plate, parent, town) in sorted(towns.items()):
            area = cs.key("tr.belde", key)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="tr.belde")
                name(area, town)
            end = closures[key][0] if key in closures else None
            cs.add(
                "area_parent",
                area_id=area,
                parent_id=find(plate, parent) or center(plate, parent) or provinces[plate],
                valid_from=None,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="day",
            )
            if key in polities:
                cs.add(
                    "polity_territory",
                    polity_id=polities[key],
                    area_id=area,
                    valid_from=None,
                    valid_from_precision="day",
                    valid_to=end,
                    valid_to_precision="day",
                )
                counts["tr.belde tied to polity"] = counts.get("tr.belde tied to polity", 0) + 1
            if key in closures:
                day, kind, targets, law, note = closures[key]
                into = [
                    found
                    for target in targets
                    if (
                        found := find(plate, target)
                        or (center(plate, target) if plate in renamed_centers else None)
                        or (
                            cs.key("tr.belde", town_key(plate, parent, target))
                            if town_key(plate, parent, target, register=False) in towns
                            else None
                        )
                    )
                    is not None
                ]
                event(kind, day, laws[law], note, [(area, "from"), *((t, "to") for t in into)])
                counts[f"tr.belde {kind}"] = counts.get(f"tr.belde {kind}", 0) + 1
        counts["tr.belde areas"] = len(towns)
        counts["tr.belde kept by the court"] = kept
        print(counts)
        if missing:
            print(f"{len(missing)} ilçe events not matched: {missing}")
        return cs.write()
