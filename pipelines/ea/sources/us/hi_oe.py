import html
import re
import subprocess
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.hi_oe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/{}"
PUBLISHER = "Hawaii Office of Elections"
TITLE = "Final summary reports by county, 1996–2024"
PARTIES = {
    "D": ("democratic-party", "democratic", "Democratic"),
    "R": ("republican-party", "republican", "Republican"),
    "L": ("libertarian-party", "libertarian", "Libertarian"),
    "G": ("green-party", "green", "Green"),
    "C": ("constitution-party", "constitution", "Constitution"),
    "NL": ("natural-law-party", "natural-law", "Natural Law"),
    "RF": ("reform-party", "reform", "Reform"),
    "A": ("HI:american-shopping-party", "american-shopping", "American Shopping"),
    "AS": ("HI:american-shopping-party", "american-shopping", "American Shopping"),
    "AA": ("HI:aloha-aina", "aloha-aina", "Aloha Aina"),
    "S": ("american-solidarity-party", "american-solidarity", "American Solidarity"),
    "SL": ("party-for-socialism-and-liberation", "socialism-and-liberation", "Party for Socialism and Liberation"),
    "W": ("we-the-people", "we-the-people", "We the People"),
    "N": (None, "nonpartisan", "Nonpartisan"),
    "F": (None, "free-energy", "Free Energy"),
    "I": (None, "independent", "Hawaii Independent"),
}
NAMES = {
    (1998, "PEABODY"): "George Peabody",
    (1998, "KUHELEAUMOKUNAWAALOABULA'"): "Kuheleaumokunawaaloabula'",
    (2002, "HILL"): "Bu La'ia Hill",
    (2002, "RYAN"): "Tracy Ryan",
    (2002, "BREWER"): "Jim Brewer",
    (2002, "CUNNINGHAM"): "Daniel Cunningham",
    (2018, "BREWER"): "Jim Brewer",
    (2018, "TERUYA"): "Terrence Teruya",
}
KALAWAO = (
    "The Office of Elections reports Kalawao County's precinct with Maui County, so Maui's figures here include "
    "Kalawao's."
)
PRIMARY = {"DE": "D", "RE": "R", "LI": "L", "GR": "G", "NP": "N", "NO": "N"}
COUNTER = re.compile(r"(?i)^(blank|over|under)\s*votes|turnout")
LINE = re.compile(
    r"^(?:\((?P<pre>[A-Z]+) ?\)\s*)?(?P<name>.+?)(?:\s+\((?P<post>[A-Z]+)\))?\s+(?P<votes>\d[\d,]*)\s+\d+\.\d%?$"
)
PRECINCTS = re.compile(r"^(?P<title>.*?)\s*(?:\b\d+\s+of\s+\d+(?:\s+[\d.]+%?)?|\d+/\s*\d+/\s*\d+)$")
COUNTIES = {"cch": "Honolulu", "coh": "Hawaii", "cok": "Kauai", "com": "Maui"}
FILES = {
    (1996, "general", "cch"): (
        "20160526232114",
        "http://files.hawaii.gov:80/elections/files/results/1996/general/cch.htm",
    ),
    (1996, "general", "coh"): (
        "20160526223605",
        "http://files.hawaii.gov:80/elections/files/results/1996/general/coh.htm",
    ),
    (1996, "general", "cok"): (
        "20160526225430",
        "http://files.hawaii.gov:80/elections/files/results/1996/general/cok.htm",
    ),
    (1996, "general", "com"): (
        "20160526230853",
        "http://files.hawaii.gov:80/elections/files/results/1996/general/com.htm",
    ),
    (1996, "primary", "cch"): (
        "20160526230903",
        "http://files.hawaii.gov:80/elections/files/results/1996/primary/cch.htm",
    ),
    (1996, "primary", "coh"): (
        "20160526225656",
        "http://files.hawaii.gov:80/elections/files/results/1996/primary/coh.htm",
    ),
    (1996, "primary", "cok"): (
        "20160526120428",
        "http://files.hawaii.gov:80/elections/files/results/1996/primary/cok.htm",
    ),
    (1996, "primary", "com"): (
        "20160526225701",
        "http://files.hawaii.gov:80/elections/files/results/1996/primary/com.htm",
    ),
    (1998, "general", "cch"): (
        "20160526121640",
        "http://files.hawaii.gov:80/elections/files/results/1998/general/cch.htm",
    ),
    (1998, "general", "coh"): (
        "20160526223616",
        "http://files.hawaii.gov:80/elections/files/results/1998/general/coh.htm",
    ),
    (1998, "general", "cok"): (
        "20160526225711",
        "http://files.hawaii.gov:80/elections/files/results/1998/general/cok.htm",
    ),
    (1998, "general", "com"): (
        "20160526225440",
        "http://files.hawaii.gov:80/elections/files/results/1998/general/com.htm",
    ),
    (1998, "primary", "cch"): (
        "20160526225451",
        "http://files.hawaii.gov:80/elections/files/results/1998/primary/cch.htm",
    ),
    (1998, "primary", "coh"): (
        "20160526224617",
        "http://files.hawaii.gov:80/elections/files/results/1998/primary/coh.htm",
    ),
    (1998, "primary", "cok"): (
        "20160526232134",
        "http://files.hawaii.gov:80/elections/files/results/1998/primary/cok.htm",
    ),
    (1998, "primary", "com"): (
        "20160526223802",
        "http://files.hawaii.gov:80/elections/files/results/1998/primary/com.htm",
    ),
    (2000, "general", "cch"): (
        "20160526230913",
        "http://files.hawaii.gov:80/elections/files/results/2000/general/cch.htm",
    ),
    (2000, "general", "coh"): (
        "20160526231023",
        "http://files.hawaii.gov:80/elections/files/results/2000/general/coh.htm",
    ),
    (2000, "general", "cok"): (
        "20160526223807",
        "http://files.hawaii.gov:80/elections/files/results/2000/general/cok.htm",
    ),
    (2000, "general", "com"): (
        "20160526231028",
        "http://files.hawaii.gov:80/elections/files/results/2000/general/com.htm",
    ),
    (2000, "primary", "cch"): (
        "20160824010209",
        "http://files.hawaii.gov:80/elections/files/results/2000/primary/cch.pdf",
    ),
    (2000, "primary", "coh"): (
        "20160824003612",
        "http://files.hawaii.gov:80/elections/files/results/2000/primary/coh.pdf",
    ),
    (2000, "primary", "cok"): (
        "20160824011025",
        "http://files.hawaii.gov:80/elections/files/results/2000/primary/cok.pdf",
    ),
    (2000, "primary", "com"): (
        "20160824010213",
        "http://files.hawaii.gov:80/elections/files/results/2000/primary/com.pdf",
    ),
    (2002, "general", "cch"): (
        "20160824003624",
        "http://files.hawaii.gov:80/elections/files/results/2002/general/cch.pdf",
    ),
    (2002, "general", "coh"): (
        "20160824003629",
        "http://files.hawaii.gov:80/elections/files/results/2002/general/coh.pdf",
    ),
    (2002, "general", "cok"): (
        "20160824003634",
        "http://files.hawaii.gov:80/elections/files/results/2002/general/cok.pdf",
    ),
    (2002, "general", "com"): (
        "20160824004057",
        "http://files.hawaii.gov:80/elections/files/results/2002/general/com.pdf",
    ),
    (2002, "primary", "cch"): (
        "20160824010226",
        "http://files.hawaii.gov:80/elections/files/results/2002/primary/cch.pdf",
    ),
    (2002, "primary", "coh"): (
        "20160824010231",
        "http://files.hawaii.gov:80/elections/files/results/2002/primary/coh.pdf",
    ),
    (2002, "primary", "cok"): (
        "20160824004102",
        "http://files.hawaii.gov:80/elections/files/results/2002/primary/cok.pdf",
    ),
    (2002, "primary", "com"): (
        "20160824003639",
        "http://files.hawaii.gov:80/elections/files/results/2002/primary/com.pdf",
    ),
    (2004, "general", "cch"): (
        "20160824010240",
        "http://files.hawaii.gov:80/elections/files/results/2004/general/cch.pdf",
    ),
    (2004, "general", "coh"): (
        "20160824004127",
        "http://files.hawaii.gov:80/elections/files/results/2004/general/coh.pdf",
    ),
    (2004, "general", "cok"): (
        "20160824011050",
        "http://files.hawaii.gov:80/elections/files/results/2004/general/cok.pdf",
    ),
    (2004, "general", "com"): (
        "20160824004132",
        "http://files.hawaii.gov:80/elections/files/results/2004/general/com.pdf",
    ),
    (2004, "primary", "cch"): (
        "20160824004137",
        "http://files.hawaii.gov:80/elections/files/results/2004/primary/cch.pdf",
    ),
    (2004, "primary", "coh"): (
        "20160824004143",
        "http://files.hawaii.gov:80/elections/files/results/2004/primary/coh.pdf",
    ),
    (2004, "primary", "cok"): (
        "20160824011055",
        "http://files.hawaii.gov:80/elections/files/results/2004/primary/cok.pdf",
    ),
    (2004, "primary", "com"): (
        "20160824004148",
        "http://files.hawaii.gov:80/elections/files/results/2004/primary/com.pdf",
    ),
    (2006, "general", "cch"): (
        "20160824010302",
        "http://files.hawaii.gov:80/elections/files/results/2006/general/cch.pdf",
    ),
    (2006, "general", "coh"): (
        "20160824010307",
        "http://files.hawaii.gov:80/elections/files/results/2006/general/coh.pdf",
    ),
    (2006, "general", "cok"): (
        "20160824010312",
        "http://files.hawaii.gov:80/elections/files/results/2006/general/cok.pdf",
    ),
    (2006, "general", "com"): (
        "20160824003701",
        "http://files.hawaii.gov:80/elections/files/results/2006/general/com.pdf",
    ),
    (2006, "primary", "cch"): (
        "20160824011114",
        "http://files.hawaii.gov:80/elections/files/results/2006/primary/cch.pdf",
    ),
    (2006, "primary", "coh"): (
        "20160824010324",
        "http://files.hawaii.gov:80/elections/files/results/2006/primary/coh.pdf",
    ),
    (2006, "primary", "cok"): (
        "20160824003712",
        "http://files.hawaii.gov:80/elections/files/results/2006/primary/cok.pdf",
    ),
    (2006, "primary", "com"): (
        "20160824003718",
        "http://files.hawaii.gov:80/elections/files/results/2006/primary/com.pdf",
    ),
    (2008, "general", "cch"): (
        "20160824004204",
        "http://files.hawaii.gov:80/elections/files/results/2008/general/cch.pdf",
    ),
    (2008, "general", "coh"): (
        "20160824004209",
        "http://files.hawaii.gov:80/elections/files/results/2008/general/coh.pdf",
    ),
    (2008, "general", "cok"): (
        "20160824004214",
        "http://files.hawaii.gov:80/elections/files/results/2008/general/cok.pdf",
    ),
    (2008, "general", "com"): (
        "20160824011119",
        "http://files.hawaii.gov:80/elections/files/results/2008/general/com.pdf",
    ),
    (2008, "primary", "cch"): (
        "20160824011130",
        "http://files.hawaii.gov:80/elections/files/results/2008/primary/cch.pdf",
    ),
    (2008, "primary", "coh"): (
        "20160824004219",
        "http://files.hawaii.gov:80/elections/files/results/2008/primary/coh.pdf",
    ),
    (2008, "primary", "cok"): (
        "20160824010352",
        "http://files.hawaii.gov:80/elections/files/results/2008/primary/cok.pdf",
    ),
    (2008, "primary", "com"): (
        "20160824011136",
        "http://files.hawaii.gov:80/elections/files/results/2008/primary/com.pdf",
    ),
    (2010, "general", "cch"): (
        "20160824010357",
        "http://files.hawaii.gov:80/elections/files/results/2010/general/cch.pdf",
    ),
    (2010, "general", "coh"): (
        "20160824005050",
        "http://files.hawaii.gov:80/elections/files/results/2010/general/coh.pdf",
    ),
    (2010, "general", "cok"): (
        "20160824005051",
        "http://files.hawaii.gov:80/elections/files/results/2010/general/cok.pdf",
    ),
    (2010, "general", "com"): (
        "20160824011801",
        "http://files.hawaii.gov:80/elections/files/results/2010/general/com.pdf",
    ),
    (2010, "primary", "cch"): (
        "20160824003734",
        "http://files.hawaii.gov:80/elections/files/results/2010/primary/cch.pdf",
    ),
    (2010, "primary", "coh"): (
        "20160824003742",
        "http://files.hawaii.gov:80/elections/files/results/2010/primary/coh.pdf",
    ),
    (2010, "primary", "cok"): (
        "20160824011811",
        "http://files.hawaii.gov:80/elections/files/results/2010/primary/cok.pdf",
    ),
    (2010, "primary", "com"): (
        "20160824005057",
        "http://files.hawaii.gov:80/elections/files/results/2010/primary/com.pdf",
    ),
    (2012, "general", "cch"): (
        "20160910213541",
        "http://files.hawaii.gov/elections/files/results/2012/general/cch.pdf",
    ),
    (2012, "general", "coh"): (
        "20160824011828",
        "http://files.hawaii.gov:80/elections/files/results/2012/general/coh.pdf",
    ),
    (2012, "general", "cok"): (
        "20160824003751",
        "http://files.hawaii.gov:80/elections/files/results/2012/general/cok.pdf",
    ),
    (2012, "general", "com"): (
        "20160824011833",
        "http://files.hawaii.gov:80/elections/files/results/2012/general/com.pdf",
    ),
    (2012, "primary", "cch"): (
        "20170202055438",
        "http://files.hawaii.gov/elections/files/results/2012/primary/cch.pdf",
    ),
    (2012, "primary", "coh"): (
        "20160824010419",
        "http://files.hawaii.gov:80/elections/files/results/2012/primary/coh.pdf",
    ),
    (2012, "primary", "cok"): (
        "20160824003801",
        "http://files.hawaii.gov:80/elections/files/results/2012/primary/cok.pdf",
    ),
    (2012, "primary", "com"): (
        "20160824011845",
        "http://files.hawaii.gov:80/elections/files/results/2012/primary/com.pdf",
    ),
    (2014, "general", "cch"): (
        "20160528153448",
        "http://files.hawaii.gov:80/elections/files/results/2014/general/cch.pdf",
    ),
    (2014, "general", "coh"): (
        "20160824005106",
        "http://files.hawaii.gov:80/elections/files/results/2014/general/coh.pdf",
    ),
    (2014, "general", "cok"): (
        "20160824005111",
        "http://files.hawaii.gov:80/elections/files/results/2014/general/cok.pdf",
    ),
    (2014, "general", "com"): (
        "20160824003811",
        "http://files.hawaii.gov:80/elections/files/results/2014/general/com.pdf",
    ),
    (2014, "primary", "cch"): (
        "20160816171537",
        "http://files.hawaii.gov/elections/files/results/2014/primary/cch.pdf",
    ),
    (2014, "primary", "coh"): (
        "20160824005122",
        "http://files.hawaii.gov:80/elections/files/results/2014/primary/coh.pdf",
    ),
    (2014, "primary", "cok"): (
        "20160824005127",
        "http://files.hawaii.gov:80/elections/files/results/2014/primary/cok.pdf",
    ),
    (2014, "primary", "com"): (
        "20160824003822",
        "http://files.hawaii.gov:80/elections/files/results/2014/primary/com.pdf",
    ),
    (2016, "general", "cch"): (
        "20171203061503",
        "http://files.hawaii.gov/elections/files/results/2016/general/cch.pdf",
    ),
    (2016, "general", "coh"): (
        "20171203070115",
        "http://files.hawaii.gov/elections/files/results/2016/general/coh.pdf",
    ),
    (2016, "general", "cok"): (
        "20171203072446",
        "http://files.hawaii.gov/elections/files/results/2016/general/cok.pdf",
    ),
    (2016, "general", "com"): (
        "20171203085312",
        "http://files.hawaii.gov/elections/files/results/2016/general/com.pdf",
    ),
    (2016, "primary", "cch"): (
        "20161020142246",
        "http://files.hawaii.gov:80/elections/files/results/2016/primary/cch.pdf",
    ),
    (2016, "primary", "coh"): (
        "20170221060542",
        "http://files.hawaii.gov/elections/files/results/2016/primary/coh.pdf",
    ),
    (2016, "primary", "cok"): (
        "20170221060553",
        "http://files.hawaii.gov/elections/files/results/2016/primary/cok.pdf",
    ),
    (2016, "primary", "com"): (
        "20170202055537",
        "http://files.hawaii.gov/elections/files/results/2016/primary/com.pdf",
    ),
    (2018, "general", "cch"): (
        "20190524044153",
        "http://files.hawaii.gov/elections/files/results/2018/general/cch.pdf",
    ),
    (2018, "general", "coh"): (
        "20201024215740",
        "https://files.hawaii.gov/elections/files/results/2018/general/coh.pdf",
    ),
    (2018, "general", "cok"): (
        "20201024210723",
        "https://files.hawaii.gov/elections/files/results/2018/general/cok.pdf",
    ),
    (2018, "general", "com"): (
        "20201031022255",
        "https://files.hawaii.gov/elections/files/results/2018/general/com.pdf",
    ),
    (2018, "primary", "cch"): (
        "20190524052444",
        "http://files.hawaii.gov/elections/files/results/2018/primary/cch.pdf",
    ),
    (2018, "primary", "coh"): (
        "20201101114843",
        "https://files.hawaii.gov/elections/files/results/2018/primary/coh.pdf",
    ),
    (2018, "primary", "cok"): (
        "20201025104304",
        "https://files.hawaii.gov/elections/files/results/2018/primary/cok.pdf",
    ),
    (2018, "primary", "com"): (
        "20201031215248",
        "https://files.hawaii.gov/elections/files/results/2018/primary/com.pdf",
    ),
    (2020, "general", "cch"): (
        "20220925215719",
        "https://files.hawaii.gov/elections/files/results/2020/general/cch.pdf",
    ),
    (2020, "general", "coh"): (
        "20220925214745",
        "https://files.hawaii.gov/elections/files/results/2020/general/coh.pdf",
    ),
    (2020, "general", "cok"): (
        "20220814075600",
        "https://files.hawaii.gov/elections/files/results/2020/general/cok.pdf",
    ),
    (2020, "general", "com"): (
        "20220814074346",
        "https://files.hawaii.gov/elections/files/results/2020/general/com.pdf",
    ),
    (2020, "primary", "cch"): (
        "20200928164928",
        "https://files.hawaii.gov/elections/files/results/2020/primary/cch.pdf",
    ),
    (2020, "primary", "coh"): (
        "20200928164640",
        "https://files.hawaii.gov/elections/files/results/2020/primary/coh.pdf",
    ),
    (2020, "primary", "cok"): (
        "20200928164514",
        "https://files.hawaii.gov/elections/files/results/2020/primary/cok.pdf",
    ),
    (2020, "primary", "com"): (
        "20200928160323",
        "https://files.hawaii.gov/elections/files/results/2020/primary/com.pdf",
    ),
    (2022, "general", "cch"): (
        "20241012024419",
        "https://files.hawaii.gov/elections/files/results/2022/general/cch.pdf",
    ),
    (2022, "general", "coh"): (
        "20240922171616",
        "https://files.hawaii.gov/elections/files/results/2022/general/coh.pdf",
    ),
    (2022, "general", "cok"): (
        "20241003055003",
        "https://files.hawaii.gov/elections/files/results/2022/general/cok.pdf",
    ),
    (2022, "general", "com"): (
        "20240927062751",
        "https://files.hawaii.gov/elections/files/results/2022/general/com.pdf",
    ),
    (2022, "primary", "cch"): (
        "20221207191201",
        "https://files.hawaii.gov/elections/files/results/2022/primary/cch.pdf",
    ),
    (2022, "primary", "coh"): (
        "20221207212029",
        "https://files.hawaii.gov/elections/files/results/2022/primary/coh.pdf",
    ),
    (2022, "primary", "cok"): (
        "20221207210756",
        "https://files.hawaii.gov/elections/files/results/2022/primary/cok.pdf",
    ),
    (2022, "primary", "com"): (
        "20221122083754",
        "https://files.hawaii.gov/elections/files/results/2022/primary/com.pdf",
    ),
    (2024, "general", "cch"): (
        "20250519140331",
        "https://files.hawaii.gov/elections/files/results/2024/General/cch.pdf",
    ),
    (2024, "general", "coh"): (
        "20250519160300",
        "https://files.hawaii.gov/elections/files/results/2024/General/coh.pdf",
    ),
    (2024, "general", "cok"): (
        "20250519155125",
        "https://files.hawaii.gov/elections/files/results/2024/General/cok.pdf",
    ),
    (2024, "general", "com"): (
        "20250519150241",
        "https://files.hawaii.gov/elections/files/results/2024/General/com.pdf",
    ),
    (2024, "primary", "cch"): (
        "20241105234601",
        "https://files.hawaii.gov/elections/files/results/2024/Primary/cch.pdf",
    ),
    (2024, "primary", "coh"): (
        "20241105234007",
        "https://files.hawaii.gov/elections/files/results/2024/Primary/coh.pdf",
    ),
    (2024, "primary", "cok"): (
        "20241105233032",
        "https://files.hawaii.gov/elections/files/results/2024/Primary/cok.pdf",
    ),
    (2024, "primary", "com"): (
        "20241105223033",
        "https://files.hawaii.gov/elections/files/results/2024/Primary/com.pdf",
    ),
}
STATEWIDE = {
    (year, stage): "https://files.hawaii.gov/elections/files/results/"
    + f"{year}/{stage.title() if year == 2024 else stage}/histatewide.{'htm' if year <= 2000 else 'pdf'}"
    for year in range(1996, 2025, 2)
    for stage in ("general", "primary")
}
UNASSIGNED = (
    "The Office of Elections' statewide summary counts more ballots than its four county reports, which were printed "
    "earlier. The difference is shown as votes not assigned to any county."
)


def fetch() -> None:
    for url in STATEWIDE.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            time.sleep(30)
            raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1])
    for stamp, url in FILES.values():
        address = WAYBACK.format(stamp, url)
        try:
            raw.cached(SOURCE, address)
        except LookupError:
            for attempt in range(6):
                time.sleep(4 + 60 * attempt)
                try:
                    raw.fetch(SOURCE, address, suffix="." + url.rsplit(".", 1)[1])
                    break
                except httpx.TransportError:
                    if attempt == 5:
                        raise


def ruled(text: str) -> list[str]:
    lines: list[str] = []
    fixed = "|" not in text and "│" not in text
    for page in re.split(r"\f|\n(?=[^\n]*\bPage +\d{3}\b)", text, flags=re.IGNORECASE):
        columns: dict[int, list[str]] = {}
        for line in page.splitlines():
            if fixed:
                if len(line) > 47 and not re.search(r"(?i)\bpage +\d{3}\b|printed on", line):
                    for index in range(3):
                        columns.setdefault(index, []).append(line[index * 47 : (index + 1) * 47].strip())
                continue
            line = re.sub(r"[│╞╡╪├┤┼╒╕╤╘╛╧]", "|", line)
            if "|" in line:
                for index, cell in enumerate(line.strip().strip("|").split("|")):
                    columns.setdefault(index, []).append(cell.strip())
        for index in sorted(columns):
            lines.extend(columns[index])
    return lines


def streams(path: Path) -> list[str]:
    if path.suffix == ".htm":
        return ruled(html.unescape(re.sub(r"<[^>]+>", "", path.read_text(errors="replace"))))
    layout = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    if "│" in layout:
        return ruled(layout)
    xhtml = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    lines: list[str] = []
    for page in xhtml.split("<page ")[1:]:
        words = [
            (float(x0), float(y0), float(x1), html.unescape(text))
            for x0, y0, x1, text in re.findall(
                r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)</word>', page
            )
        ]
        if not words:
            continue
        left = min(word[0] for word in words)
        width = (max(word[2] for word in words) - left) / 3
        columns: dict[int, list[tuple[float, float, str]]] = {}
        for x0, y0, _, text in words:
            columns.setdefault(min(int((x0 - left + 4) // width), 2), []).append((y0, x0, text))
        for index in sorted(columns):
            rows: list[tuple[float, list[tuple[float, str]]]] = []
            for y0, x0, text in sorted(columns[index]):
                if rows and abs(rows[-1][0] - y0) < 2.5:
                    rows[-1][1].append((x0, text))
                else:
                    rows.append((y0, [(x0, text)]))
            lines.extend(" ".join(text for _, text in sorted(row)) for _, row in rows)
    return lines


def parse() -> None:
    rows: list[dict[str, object]] = []
    reports = [
        (year, stage, county, WAYBACK.format(stamp, url)) for (year, stage, county), (stamp, url) in FILES.items()
    ] + [(year, stage, "hi", url) for (year, stage), url in STATEWIDE.items()]
    for year, stage, county, address in reports:
        title = ""
        pending = ""
        closed = True
        for line in streams(raw.cached(SOURCE, address).path):
            spaced = re.match(r"^\s*(\S.*[A-Za-z)\]])\s{4,}\d+$", line)
            line = re.sub(r"\s+", " ", line).strip()
            if spaced:
                title = re.sub(r"\s+", " ", spaced[1])
                pending = ""
                closed = False
                continue
            if not line or re.fullmatch(r"[-=─═\s]+|[\d./ ]+|(?i:number to vote for:? \d+)", line):
                continue
            if found := PRECINCTS.match(line):
                title = found["title"] if re.search(r"[A-Za-z]", found["title"]) else pending
                pending = ""
                closed = False
                continue
            if (found := LINE.match(line)) and COUNTER.search(found["name"]):
                closed = True
                continue
            if found := LINE.match(line):
                closed = False
                party = ""
                office = title
                if heading := re.match(r"^\((\w+)\) (.+)$|^(.+?) - (\w+)$|^(.+?) HAWAII \((\w+)\)$", title):
                    party = next(group for group in (heading[1], heading[4], heading[6]) if group)
                    office = next(group for group in (heading[2], heading[3], heading[5]) if group)
                if re.match(r"(?i)u\.? ?s\.? senator", office):
                    kind = "senate"
                elif re.match(r"(?i)president", office):
                    kind = "president"
                elif re.match(r"(?i)governor", office):
                    kind = "governor"
                else:
                    continue
                if found["name"] in ("YES", "NO"):
                    continue
                rows.append(
                    {
                        "year": year,
                        "stage": stage,
                        "county": county,
                        "title": title,
                        "office": kind,
                        "primary": ("AA" if party == "A" and year >= 2020 else PRIMARY.get(party, party))
                        if stage == "primary"
                        else "",
                        "name": re.sub(r" For PRESIDENT$", "", found["name"]),
                        "party": "AA"
                        if (found["pre"] or found["post"]) == "A" and year >= 2020
                        else found["pre"] or found["post"] or "",
                        "votes": int(found["votes"].replace(",", "")),
                    }
                )
                continue
            pending = line
            if closed:
                title = line
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("year", "stage", "office", "primary", "title")
        .agg(pl.col("county").n_unique(), pl.col("name").n_unique(), pl.col("votes").sum())
        .sort("year", "stage", "office", "primary")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    statewide: dict[tuple[int, str, str], dict[str, int]] = {}
    for year, stage, title, candidate, votes in (
        frame.filter(pl.col("county") == "hi").select("year", "stage", "title", "name", "votes").iter_rows()
    ):
        statewide.setdefault((year, stage, title), {})[candidate] = votes
    frame = frame.filter(pl.col("county") != "hi")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Hawaii's president, U.S. Senate, and governor elections and primaries 1996–2024 by county from "
            "the Office of Elections' final summary reports",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def cased(word: str) -> str:
            if found := re.fullmatch(r"(Mc|Mac|De|Di|La|Le|Van)([A-Z'-]+)", word):
                return found[1] + found[2].capitalize()
            return word.title() if word.isupper() else word

        def head(text: str) -> str:
            return re.split(r"\s*[/\\]\s*| AND ", text)[0].strip()

        def surname(text: str) -> str:
            text = head(text)
            if "," in text:
                return plain(text.split(",")[0])
            words = [word for word in text.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        def shown(text: str) -> str:
            text = head(text)
            if "," not in text:
                return " ".join(cased(word) for word in text.split())
            last, first = text.split(",", 1)
            suffix = ""
            if found := re.search(r",\s*(Jr\.?|Sr\.?|II|III|IV)$", first, re.IGNORECASE):
                suffix = ", " + found[1].replace("JR", "Jr").replace("SR", "Sr")
                first = first[: found.start()]
            return " ".join(cased(word) for word in f"{first.strip()} {last.strip()}".split()) + suffix

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.hawaii.gov/election-results/",
            license="Public record",
        )
        units: dict[str, str] = {
            code: str(unit)
            for code, unit in conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code in ('15001', '15003', '15007', '15009')"
            ).fetchall()
        }
        counties = {"coh": units["15001"], "cch": units["15003"], "cok": units["15007"], "com": units["15009"]}
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-HI'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        aggregate = cs.unit("us.aggregate", "HI:statewide")
        cs.add(
            "reporting_unit",
            id=aggregate,
            polity_id=cs.polity("US-HI"),
            scheme_code="us.aggregate",
            code="HI:statewide",
            parent_id=cs.unit("us.state", "15"),
            area_relation="within",
            area_id=area,
            kind="aggregate",
            valid_from=None,
            valid_to=None,
        )
        elections: dict[tuple[int, str], tuple[date, UUID]] = {}
        for value, day, election in conn.execute(
            """select x.value, e.date_from, e.id from election e join polity p on p.id = e.polity_id and p.iso_code = 'US-HI'
               join external_id x on x.entity_id = e.id and x.scheme = 'us.election'
               where e.kind in ('general', 'primary') and e.date_from >= '1996-01-01'"""
        ).fetchall():
            elections[(day.year, "primary" if value.endswith(":primary") else "general")] = (day, election)
        contests: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ '^(199[6-9]|20[0-2][0-9]).*:HI:(president|senate|governor)'"
            ).fetchall()
        )
        people: dict[str, set[UUID]] = {}
        for person, text in conn.execute(
            """select distinct cd.person_id, n.text from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id
               join name n on n.entity_id = cd.person_id and n.is_preferred
               where c.district_area_id = %s or (c.kind = 'elector_slate' and e.date_from = '1996-11-05')""",
            [area],
        ).fetchall():
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{plain(text.split()[-1])}", set()).add(person)
        slates: dict[str, dict[UUID, tuple[str, UUID | None]]] = {}
        for person, label, party in conn.execute(
            """select cd.person_id, coalesce(o.ballot_label, cd.ballot_name, ''), o.party_id from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id
               where c.kind = 'elector_slate' and e.date_from = '1996-11-05' and cd.role = 'head'"""
        ).fetchall():
            if label:
                slates.setdefault(plain(label.split()[-1]), {})[person] = (label, party)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", key) for code, (key, _, _) in PARTIES.items() if key is not None}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must already exist")
        made: set[UUID] = set()
        attached: set[tuple[int, str, str]] = set()
        used: set[str] = set()
        filled = 0
        for (year, stage, office, primary, title), lines in frame.sort("year", "stage", "office", "primary").group_by(
            "year", "stage", "office", "primary", "title", maintain_order=True
        ):
            if sorted(lines["county"].unique()) != sorted(COUNTIES):
                raise ValueError(f"{year} {title}: counties {sorted(lines['county'].unique())}")
            day, election = elections[(year, stage)]
            special = bool(re.search(r"(?i)vacan|unexpired|special", title))
            party = PARTIES[primary] if primary else None
            key = f"{day}:HI:{office}" + (":special" if special else "") + (f":{party[1]}" if party else "")
            existing = key in contests
            if (
                not existing
                and not (office == "president" and stage == "general")
                and not (office == "governor" and party is not None)
            ):
                print(f"{key}: not on file, skipped")
                continue
            contest = cs.key("us.contest", key)
            if not existing:
                if office == "president" and stage == "general":
                    ((parent, body, system),) = conn.execute(
                        """select c.id, c.body_id, c.electoral_system_id from contest c join external_id e on e.entity_id = c.id
                           and e.scheme = 'us.contest' where e.value = %s""",
                        [f"{day}:president"],
                    ).fetchall()
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="elector_slate",
                        body_id=body,
                        district_area_id=area,
                        magnitude=4,
                        round_number=1,
                        electoral_system_id=system,
                        allocation_parent_id=parent,
                        default_result_kind="counted",
                    )
                    name(contest, f"{year} presidential election in Hawaii")
                    cs.add("slug", path=f"us/hi/{day}-general/president", entity_id=contest)
                else:
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="office",
                        office_id=cs.key("us.office", "governor:HI"),
                        district_area_id=area,
                        term="full",
                        magnitude=1,
                        round_number=1,
                        party_scope_id=parties.get(primary),
                        electoral_system_id=fptp,
                        default_result_kind="counted",
                    )
                    name(contest, f"{year} Hawaii Governor {party[2]} primary")
                    cs.add("slug", path=f"us/hi/{day}-primary/governor-{party[1]}", entity_id=contest)
            used.add(key)
            summed: dict[str, int] = dict(lines.group_by("name").agg(pl.col("votes").sum()).iter_rows())
            printed = statewide.get((year, stage, title), {})
            remainder = {
                candidate: printed[candidate] - votes
                for candidate, votes in summed.items()
                if candidate in printed and printed[candidate] != votes
            }
            if any(votes < 0 for votes in remainder.values()):
                raise ValueError(f"{key}: statewide summary {printed} is below the counties' {summed}")
            if printed and set(printed) != set(summed):
                print(f"{key}: statewide summary names {sorted(printed)}, counties {sorted(summed)}; not compared")
                remainder = {}
            result_set = cs.key(f"{SOURCE}.result_set", key)
            previous = None
            if result_set not in cs.minted:
                if not remainder:
                    continue
                previous = result_set
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:statewide")
                if result_set not in cs.minted:
                    continue
            options: list[tuple[UUID, str, str]] = []
            outcomes: list[tuple[UUID, str, int | None]] = []
            on_file: dict[UUID, int] = {}
            default = True
            if existing:
                options = conn.execute(
                    """select id, kind, ballot_label from contest_option
                       where contest_id = %s and kind in ('candidate', 'ticket') and parent_option_id is null""",
                    [contest],
                ).fetchall()
                for *fields, flagged in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id, is_default from result_set
                       where contest_id = %s and coalesce(id = %s, is_default)""",
                    [contest, previous],
                ).fetchall():
                    if fields[0] == previous:
                        fields[5] = "superseded"
                        default = flagged
                    cs.add(
                        "result_set",
                        **dict(
                            zip(
                                (
                                    "id",
                                    "election_id",
                                    "contest_id",
                                    "stage",
                                    "kind",
                                    "status",
                                    "source_id",
                                    "as_of",
                                    "completeness",
                                    "supersedes_id",
                                ),
                                fields,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )
                outcomes = conn.execute(
                    """select o.option_id, o.kind, o.seats from outcome o
                       join result_set rs on rs.id = o.result_set_id
                       where rs.contest_id = %s and coalesce(rs.id = %s, rs.is_default)""",
                    [contest, previous],
                ).fetchall()
                if previous is not None:
                    for option, kind, seats in outcomes:
                        cs.add("outcome", result_set_id=previous, option_id=option, kind=kind, seats=seats)
                on_file = dict(
                    conn.execute(
                        """select o.id, sum(v.value)::bigint from result_value v
                           join result_set rs on rs.k = v.result_set_k and rs.is_default
                           join contest_option o on o.k = v.option_k
                           where rs.contest_id = %s and v.measure = 1 group by 1""",
                        [contest],
                    ).fetchall()
                )
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for candidate, code, votes in (
                lines.group_by("name", "party", maintain_order=True)
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
                .iter_rows()
            ):
                matches = [option for option, _, text in options if plain(text.split()[-1]) == surname(candidate)]
                if len(matches) > 1:
                    matches = [option for option, _, text in options if plain(text) == plain(shown(candidate))]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if matches:
                    option = matches[0]
                elif not existing and office == "president" and len(found := slates.get(surname(candidate), {})) == 1:
                    person, (label, party_id) = next(iter(found.items()))
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket",
                        party_id=party_id,
                        ballot_label=label,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=party_id is None,
                    )
                else:
                    label = NAMES.get((year, head(candidate)), shown(candidate))
                    if " " not in label and (year, head(candidate)) not in NAMES:
                        raise ValueError(f"{key}: {candidate} is a surname only and matches no option")
                    probes = [plain(label), f"{plain(label.split()[0])}|{surname(candidate)}"]
                    person = next(
                        (next(iter(found)) for probe in probes if len(found := people.get(probe, set())) == 1), None
                    )
                    if person is None:
                        person = cs.key(f"{SOURCE}.person", f"HI:{slug(label)}")
                    if person in cs.minted and person not in made:
                        made.add(person)
                        cs.add("person", id=person)
                        name(person, label)
                        for probe in probes:
                            people.setdefault(probe, set()).add(person)
                    option = cs.key("us.option", f"{key}:{person}")
                    if option in cs.minted:
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="ticket" if office == "president" and stage == "general" else "candidate",
                            party_id=parties.get(code or primary),
                            ballot_label=label,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=label,
                            is_independent=(code or primary) not in parties,
                        )
                if option in totals:
                    raise ValueError(f"{key}: two candidates share an option with {candidate}")
                if option in on_file and abs(on_file[option] - votes) > max(50, on_file[option] // 100):
                    print(f"{key}: {candidate} has {votes}, {on_file[option]} on file")
                totals[option] = votes + remainder.get(candidate, 0)
                picked[candidate] = option
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=default,
                supersedes_id=previous,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=KALAWAO)
            for county in sorted(COUNTIES):
                if (year, stage, county) not in attached:
                    attached.add((year, stage, county))
                    stamp, url = FILES[(year, stage, county)]
                    cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, url)), source)
            if remainder:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNASSIGNED)
                if (year, stage, "hi") not in attached:
                    attached.add((year, stage, "hi"))
                    cs.artifact(raw.cached(SOURCE, STATEWIDE[(year, stage)]), source)
            for candidate, votes in remainder.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=aggregate,
                    option_id=picked[candidate],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=32,
                    value=votes,
                )
            for candidate, county, votes in lines.select("name", "county", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[county],
                    option_id=picked[candidate],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                winner = max(totals, key=totals.__getitem__)
                if office == "president" and stage == "general":
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="won", seats=4)
                elif party is None:
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
                elif office != "president":
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="won", seats=None)
            filled += 1
        unread = sorted(value for value in contests if value not in used and not re.search(r":senate:\d", value))
        print(filled, "contests filled; on file but not read:", unread)
        return cs.write()
