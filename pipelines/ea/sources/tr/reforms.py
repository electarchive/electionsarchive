import re
import subprocess
from urllib.parse import quote

import polars as pl

from ea import raw
from ea.sources.tr import ysk_candidates_2009
from ea.sources.tr.ysk import PROVINCES, upper
from ea.store import DATA

SOURCE = "tr.reforms"
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
GUIDE_6360 = "https://www.tbb.gov.tr/sites/default/files/storage/userfiles/rehber_ve_bilgi_notlari/6360_torba_personel_rehber.pdf"
SPELLINGS = {"AFYON": "AFYONKARAHİSAR"}
ROW = re.compile(
    r"^\s*(?:(\d+)\s+)?([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ .'()-]*?)\s{2,}(?:(\d\d\.\d\d\.\d{4})|CUMHURİYET ÖNCESİ)"
    r"(?:\s+(\S+(?: \S+)*?))?\s*(\d\d\.\d\d\.\d{4}-\d+)?\s*$"
)

ELEVATED = {
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
    "Law 5216 provisional art. 2: a circle around the valilik building, 20 km up to 1 million people, "
    "30 km to 2 million, 50 km above, inside the province"
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
    raw.fetch(SOURCE, GAZETTE_6447, suffix=".htm")
    raw.fetch(SOURCE, GUIDE_6360, suffix=".pdf")


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
