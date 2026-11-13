import csv
import json
import re
from datetime import datetime
from html import unescape
from zoneinfo import ZoneInfo

from ea import raw
from ea.store import DATA

SOURCE = "us.ballots"
PATTERN = re.compile(
    r"(?i)^https://web\.archive\.org/web/(\d{14})id_/https://apps\.arizona\.vote/info/bps/2022-general-election/33/0(?:\?.*)?$"
)
PHOENIX = ZoneInfo("America/Phoenix")
COLUMNS = ["tabulated", "early_left", "provisional_left", "ready", "uncounted", "percent"]
OUT = DATA / "tmp" / "az_bps_2022.tsv"


def cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", c))).strip()
        for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
    ]


def number(text: str) -> str:
    text = text.replace(",", "").replace("%", "").strip()
    return "" if text in {"", "--"} else text


def parse(html: str) -> tuple[str, list[list[str]]]:
    stamp = re.search(r"Last Updated On (\d+/\d+/\d{4} \d+:\d+:\d+ [AP]M)", html)
    if stamp is None:
        return "", []
    page = datetime.strptime(stamp.group(1), "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=PHOENIX).isoformat()
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL):
        c = cells(row)
        if len(c) != 8 or c[0] == "":
            continue
        if c[0] == "State Total":
            rows.append(["", "State Total", *[number(x) for x in c[2:]]])
        else:
            updated = datetime.strptime(c[0], "%m/%d/%Y %H:%M").replace(tzinfo=PHOENIX).isoformat() if c[0] else ""
            rows.append([updated, c[1], *[number(x) for x in c[2:]]])
    return page, rows


def main() -> None:
    index = json.loads((DATA / "raw" / SOURCE / "index.json").read_text())
    urls = sorted(u for u in index if PATTERN.match(u))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with OUT.open("w", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["capture", "page_updated", "row_updated", "county", *COLUMNS])
        for url in urls:
            artifact = raw.cached(SOURCE, url)
            page, rows = parse(artifact.path.read_text(encoding="utf-8"))
            if not rows:
                print(f"skipped {url}: no table")
            for r in rows:
                w.writerow([PATTERN.match(url).group(1), page, *r])
                count += 1
    print(f"{len(urls)} captures, {count} rows -> {OUT}")


if __name__ == "__main__":
    main()
