import json
import re
import sys
from datetime import date, timedelta
from math import ceil
from pathlib import Path

from ea import raw
from ea.store import connect

ITEMS = {
    "1930": "19291930",
    "1932": "19311932",
    "1934": "19331934",
    "1936": "19351936",
    "1938": "19371939",
    "1940": "19401941",
    "1942": "1942",
    "1944": "19431945",
    "1946": "19461948",
    "1950": "19501951",
    "1952": "1952",
    "1954": "19531955",
    "1956": "1956",
    "1958": "19571958",
    "1960": "19591960",
    "1962": "19611963",
    "1964": "1964",
    "1966": "19651966",
    "1968": "19671968",
}
HEADING = re.compile(r"Representatives?\s+in\s+(the\s+\S+\s+)?Congress", re.IGNORECASE)
PARTY = re.compile(r"REPUBLICAN|DEMOCRATIC|PROGRESSIVE|PROHIBITION")
WORD = re.compile(r'<WORD coords="(\d+),(\d+),(\d+),(\d+)"[^>]*>([^<]*)</WORD>')
TOTALS = re.compile(r"^T[o0]ta[l1I]s?\.?$")
HEAD = 0.09
TOP = 0.075
BOTTOM = 0.985


def day_of(year: int) -> str:
    first = date(year, 11, 1)
    return (first + timedelta(days=(-first.weekday()) % 7 + 1)).isoformat()


def build(item: str, years: list[int]) -> str:
    url = f"https://archive.org/download/electionstatisti{item}mass/electionstatisti{item}mass_djvu.xml"
    xml = raw.fetch("us.scans", url, suffix=".xml").path.read_text(errors="ignore").split("<OBJECT")[1:]
    pages = []
    for leaf, body in enumerate(xml):
        words = [(int(l), int(b), int(r), int(t), w.strip()) for l, b, r, t, w in WORD.findall(body)]
        width, height = (int(re.search(rf'{key}="(\d+)"', body).group(1)) for key in ("width", "height"))
        pages.append((leaf, words, width, height))
    heads = [
        leaf
        for leaf, words, *_ in pages
        if HEADING.search(text := " ".join(w for *_, w in words[:40])) and not PARTY.search(text)
    ]
    clusters: list[list[int]] = []
    for leaf in heads:
        if clusters and leaf - clusters[-1][-1] <= 3:
            clusters[-1].append(leaf)
        else:
            clusters.append([leaf])
    clusters = clusters[-len(years) :]
    if len(clusters) != len(years):
        return f"{item}: {len(clusters)} Representatives blocks for {len(years)} years, leaves {heads}"
    out = []
    for year, cluster in zip(years, clusters, strict=True):
        contests: dict[str, dict[str, object]] = {}
        sections: list[dict[str, object]] = []
        district = 1
        first = cluster[0]
        while first > 0 and "Continu" in " ".join(w for *_, w in pages[first][1][:40]):
            first -= 1
        for leaf, words, width, height in pages[first : cluster[-1] + 1]:
            closings = sorted(b for l, b, r, t, w in words if TOTALS.match(w) and l < 0.33 * width and t > 0.1 * height)
            top = TOP * height
            for index, closing in enumerate([*closings, None]):
                bottom = BOTTOM * height if closing is None else closing + 0.004 * height
                if bottom - top > 0.06 * height:
                    contests.setdefault(
                        f"d{district}",
                        {
                            "year": year,
                            "chamber": "house",
                            "day": day_of(year),
                            "special": False,
                            "state": "MA",
                            "district": district,
                        },
                    )
                    count = max(1, ceil((bottom - top) / height / 0.45))
                    section: dict[str, object] = {
                        "page": leaf,
                        "id": f"{leaf}-{index}",
                        "discover": f"d{district}",
                        "orientation": "counties_down",
                        "crop": [0.0, round(top / height, 4), 1.0, round(bottom / height, 4)],
                        "hint": f"This is U.S. Representative, District {district}, {year}. The candidate names are printed turned sideways above the columns; Blanks and Total Ballots are columns too.",
                    }
                    if count > 1:
                        section["bands"] = count
                        section["header"] = [0.0, round(min(1.0, (HEAD * height) / (bottom - top)), 4)]
                    sections.append(section)
                if closing is not None:
                    district += 1
                    top = bottom
        out.append((year, contests, sections))
    for year, contests, sections in out:
        with connect() as conn:
            have = conn.execute(
                "select count(*) from external_id where scheme = 'us.contest' and value ~ %s",
                [f"^{day_of(year)}:MA:house:[0-9]+$"],
            ).fetchone()[0]
        if have != len(contests):
            return f"{item} {year}: found {len(contests)} districts, database has {have}"
        manifest = {
            "polity": "US-MA",
            "publisher": "Massachusetts Secretary of the Commonwealth",
            "title": f"Election Statistics {year} (P.D. 43), Representatives in Congress by town",
            "license": "Public record",
            "file": {
                "url": f"https://archive.org/download/electionstatisti{item}mass/page/n{{page}}_w2000.jpg",
                "item": f"https://archive.org/details/electionstatisti{item}mass",
            },
            "raw_source": "us.scans",
            "unit_scheme": "town",
            "unit_codes": {"Gay Head": "25007"},
            "contests": contests,
            "pages": sections,
        }
        (Path(__file__).parent / f"ma-es-{year}.json").write_text(json.dumps(manifest, indent=2))
    return f"{item}: {[(y, len(c), len(s)) for y, c, s in out]}"


if __name__ == "__main__":
    for year, item in ITEMS.items():
        if len(sys.argv) > 1 and year not in sys.argv[1:]:
            continue
        try:
            print(build(item, [1946, 1948] if year == "1946" else [int(year)]))
        except Exception as error:
            print(item, "failed:", error)
