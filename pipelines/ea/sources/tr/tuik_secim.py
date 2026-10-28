import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "tr.tuik_secim"
VERSION = "1"
APP = "https://biruni.tuik.gov.tr/secimdagitimapp/secim.zul"
TABLE = "Seçim çevresi ve ilçelere göre milletvekili genel seçimi sonuçları"
YEARS = ("1961", "1965", "1969", "1973", "1977", "1983", "1987", "1991", "1995", "1999", "2002", "2007")
PARSED = DATA / "parsed" / SOURCE
MEASURES = (("Sandık", "boxes"), ("Kayıtlı", "registered"), ("Oy kullanan", "voters"), ("Geçerli", "valid"))
PARTS = {"İl/İlçe merkezi": "urban", "Belde/Köy": "rural"}
ITEM = r'<tr id="([^"]+)" z\.type="Lit"[^>]*>\s*<td[^>]*>([^<]+)'


def key(year: str, district: str) -> str:
    return f"{APP}?tablo=ilce&yil={year}&cevre={district}"


class App:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": raw.USER_AGENT})
        page = self.client.get(APP).text
        self.action = "https://biruni.tuik.gov.tr" + re.search(r'zk_action="([^"]+)"', page).group(1)
        self.dtid = re.search(r'z\.dtid="([^"]+)"', page).group(1)
        self.button = re.search(r'<button type="button" id="([^"]+)"[^>]*>\s*Raporu Oluştur', page).group(1)
        at = page.index(TABLE)
        item = re.findall(r'<tr id="([^"]+)" z\.type="Lit"', page[:at])[-1]
        box = re.findall(r'<div id="([^"]+)" z\.type="zul\.sel\.Libox"', page[:at])[-1]
        self.years = self.select(box, item)

    def post(self, data: list[tuple[str, str]]) -> str:
        response = self.client.post(
            self.action,
            content=urlencode([("dtid", self.dtid), *data]),
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        response.raise_for_status()
        return response.text

    def select(self, box: str, item: str) -> tuple[str, dict[str, str]]:
        text = self.post([("cmd.0", "onSelect"), ("uuid.0", box), ("data.0", item)])
        if re.search(r"<d>[^<]+_e2</d>\s*<d>visibility</d>\s*<d>true</d>", text):
            self.post([("cmd.0", "onCheck"), ("uuid.0", self.button.removesuffix("e3") + "g2"), ("data.0", "true")])
        found = re.search(r'<div id="([^"]+)" z\.type="zul\.sel\.Libox"', text)
        if found is None:
            return "", {}
        return found.group(1), {html.unescape(label).strip(): uuid for uuid, label in re.findall(ITEM, text)}

    def report(self) -> str:
        text = self.post(
            [("cmd.0", "onClick"), ("uuid.0", self.button), ("data.0", "43"), ("data.0", "9"), ("data.0", "")]
        )
        return re.search(r"<c>redirect</c>\s*<d>([^<]+)</d>", text).group(1)


def year(label: str) -> None:
    for attempt in range(6):
        try:
            app = App()
            box, years = app.years
            districts_box, districts = app.select(box, years[label])
            for district, item in districts.items():
                url = key(label, district)
                try:
                    raw.cached(SOURCE, url)
                    continue
                except LookupError:
                    pass
                app.select(districts_box, item)
                link = app.report().replace("http://", "https://")
                content = app.client.get(link).content
                if b"<tr" not in content:
                    raise ValueError(f"{label} {district}: empty report")
                raw.store(SOURCE, url, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
                print(label, district, len(content), flush=True)
            return
        except (httpx.HTTPError, AttributeError, ValueError, KeyError) as error:
            print(label, "retry", attempt, repr(error), flush=True)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"{label}: gave up")


def fetch() -> None:
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(year, YEARS))


def cells(row: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()
        for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
    ]


def parse() -> None:
    _, index = raw._index(SOURCE)
    rows: list[dict[str, object]] = []
    notes: list[dict[str, str]] = []
    for url in sorted(index):
        year, district = re.search(r"yil=(\d{4})&cevre=(.+)$", url).groups()
        text = re.sub(
            r"<script.*?</script>", "", raw.cached(SOURCE, url).path.read_bytes().decode("cp1254"), flags=re.DOTALL
        )
        table = [cells(row) for row in re.findall(r"<tr.*?</tr>", text, flags=re.DOTALL | re.IGNORECASE)]
        title = next(row[0] for row in table if row and "Seçim" in row[0] and "Sonuçları" in row[0])
        if year not in title:
            raise ValueError(f"{url}: title {title}")
        start = next(i for i, row in enumerate(table) if row and row[0] == title)
        end = next(i for i, row in enumerate(table) if i > start and any(re.fullmatch(r"\d+", cell) for cell in row))
        labels = [
            cell
            for row in table[start + 1 : end]
            for cell in row
            if cell and cell != "Seçim çevresi ve ilçe" and not cell.startswith("Seçime")
        ]
        columns = [code for prefix, code in MEASURES if any(label.startswith(prefix) for label in labels)] + [
            re.sub(r"\s+", " ", label) for label in labels if not label.startswith(tuple(p for p, _ in MEASURES))
        ]
        level = None
        for row in table[end:]:
            if not row or not any(row):
                continue
            if row[0] and not any(re.fullmatch(r"\d+", cell) for cell in row[1:]):
                notes.append({"year": year, "district": district, "text": " ".join(cell for cell in row if cell)})
                continue
            if row[0]:
                level, name = ("nation" if row[0] == "Türkiye" else "district"), row[0]
            elif len(row) > 1 and row[1] in PARTS:
                level = PARTS[row[1]]
            elif len(row) > 1 and row[1]:
                level, name = "ilce", row[1]
            else:
                continue
            numbers = [cell for cell in (row[1:] if level in ("nation", "district") else row[2:]) if cell and not re.fullmatch(r"\(\d+\)", cell)]
            if level in PARTS.values() and len(numbers) != len(columns):
                continue
            if len(numbers) != len(columns) or any(not re.fullmatch(r"-|\d+", cell) for cell in numbers):
                raise ValueError(f"{url}: {row}")
            values = [0 if cell == "-" else int(cell) for cell in numbers]
            for column, value in zip(columns, values, strict=True):
                rows.append(
                    {"year": year, "district": district, "level": level, "name": name, "column": column, "value": value}
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "ilce.parquet")
    pl.DataFrame(notes).unique().write_parquet(PARSED / "notes.parquet")
    print(frame.group_by("year", "level").agg(pl.col("name").n_unique()).sort("year", "level"))
