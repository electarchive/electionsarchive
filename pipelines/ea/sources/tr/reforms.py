import re
import subprocess
from urllib.parse import quote

import polars as pl

from ea import raw
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
}
SPELLINGS = {"AFYON": "AFYONKARAHİSAR"}
ROW = re.compile(
    r"^\s*(?:(\d+)\s+)?([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ .'()-]*?)\s{2,}(?:(\d\d\.\d\d\.\d{4})|CUMHURİYET ÖNCESİ)"
    r"(?:\s+(\S+(?: \S+)*?))?\s*(\d\d\.\d\d\.\d{4}-\d+)?\s*$"
)


def fetch() -> None:
    raw.fetch(SOURCE, ICISLERI, suffix=".pdf")
    for link in LAWS.values():
        raw.fetch(SOURCE, link, suffix=".pdf")


def parse() -> None:
    pdf = raw.cached(SOURCE, ICISLERI).path
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, text=True).stdout
    province = ""
    plates = {upper(name): f"{plate:02}" for plate, name in enumerate(PROVINCES, 1)}
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        if heading := re.match(r"^\s*\*(.+?)\*?\s*$", line):
            province = heading.group(1)
        elif match := ROW.match(line):
            rows.append(
                {
                    "plate": plates[SPELLINGS.get(province, province)],
                    "province": province,
                    "name": match.group(2).strip(),
                    "is_province_seat": match.group(1) is None,
                    "founded": match.group(3),
                    "law": match.group(4),
                    "gazette": match.group(5),
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "ilce_2019.parquet")
