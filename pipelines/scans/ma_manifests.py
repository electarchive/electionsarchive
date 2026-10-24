import json
import re
import sys
from pathlib import Path

from ea import raw
from ea.layout import day_of

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
MANUAL = {
    "1888": "amanualforusege04coolgoog",
    "1890": "amanualforusege13taylgoog",
    "1892": "amanualforusege03taylgoog",
    "1894": "amanualforusege00stowgoog",
    "1896": "amanualforusege19taylgoog",
    "1898": "amanualforusege12taylgoog",
    "1900": "amanualforusege24taylgoog",
    "1902": "amanualforusege02taylgoog",
    "1904": "amanualforusege01taylgoog",
    "1906": "amanualforusege00taylgoog",
    "1908": "amanualforusege18taylgoog",
    "1910": "amanualforusege00courgoog",
    "1912": "amanualforusege26taylgoog",
    "1914": "amanualforusege05taylgoog",
    "1916": "amanualforusege04taylgoog",
    "1918": "amanualforusege10taylgoog",
}
ORDINALS = {
    51: "first", 52: "second", 53: "third", 54: "fourth", 55: "fifth", 56: "sixth", 57: "seventh", 58: "eighth",
    59: "ninth", 60: "tieth", 61: "first", 62: "second", 63: "third", 64: "fourth", 65: "fifth", 66: "sixth",
}
HEADING = re.compile(r"Representatives?\s+in\s+(the\s+\S+\s+)?Congress", re.IGNORECASE)
PARTY = re.compile(r"REPUBLICAN|DEMOCRATIC|PROGRESSIVE|PROHIBITION")
WORD = re.compile(r'<WORD coords="(\d+),(\d+),(\d+),(\d+)"[^>]*>([^<]*)</WORD>')


def build(item: str, years: list[int], manual: bool = False) -> str:
    base = item if manual else f"electionstatisti{item}mass"
    url = f"https://archive.org/download/{base}/{base}_djvu.xml"
    xml = raw.fetch("us.scans", url, suffix=".xml").path.read_text(errors="ignore").split("<OBJECT")[1:]
    texts = [" ".join(w.strip() for *_, w in WORD.findall(body))[:400] for body in xml]
    for year in years:
        number = (year - 1788) // 2 + 1
        if manual:
            congress = re.compile(rf"{ORDINALS[number]}\s+Congress", re.IGNORECASE)
            heads = [
                leaf
                for leaf, text in enumerate(texts)
                if re.search(r"Representatives", text[:200], re.IGNORECASE) and congress.search(text[:200])
            ]
        else:
            congress = re.compile(rf"{number}(st|nd|rd|th)\s+Congress", re.IGNORECASE)
            heads = [
                leaf
                for leaf, text in enumerate(texts)
                if HEADING.search(text) and congress.search(text) and not PARTY.search(text[:200])
            ]
        if not heads:
            return f"{item} {year}: no Representatives heading for the {number}th Congress"
        clusters: list[list[int]] = []
        for leaf in heads:
            if clusters and leaf - clusters[-1][-1] <= 3:
                clusters[-1].append(leaf)
            else:
                clusters.append([leaf])
        biggest = max(clusters, key=lambda cluster: (len(cluster), cluster[0]))
        first, last = biggest[0], biggest[-1]
        first = min(first, last - 7)
        spec = {
            "year": year,
            "day": day_of(year),
            "polity": "US-MA",
            "publisher": "Massachusetts Secretary of the Commonwealth",
            "title": f"A Manual for the Use of the General Court, {year} Representatives in Congress by town"
            if manual
            else f"Election Statistics {year} (P.D. 43), Representatives in Congress by town",
            "license": "Public record",
            "file": {
                "url": f"https://archive.org/download/{base}/page/n{{page}}_w2000.jpg",
                "item": f"https://archive.org/details/{base}",
            },
            "raw_source": "us.scans",
            "unit_scheme": "town",
            "unit_codes": {"Gay Head": "25007"},
            "offices": {"house": "U.S. Representative in Congress, one table per district"},
            "leaves": list(range(first, last + 2)),
        }
        (Path(__file__).parent / "volumes" / f"ma-es-{year}.json").write_text(json.dumps(spec, indent=2))
    return f"{item}: {years}"


if __name__ == "__main__":
    (Path(__file__).parent / "volumes").mkdir(exist_ok=True)
    for year, item in {**ITEMS, **MANUAL}.items():
        if len(sys.argv) > 1 and year not in sys.argv[1:]:
            continue
        try:
            print(build(item, [1946, 1948] if year == "1946" else [int(year)], year in MANUAL))
        except Exception as error:
            print(item, "failed:", error)
