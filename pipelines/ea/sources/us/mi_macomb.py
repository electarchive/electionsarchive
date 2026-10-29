import html
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from ea import raw
from ea.sources.us import enr_clarity

SOURCE = "us.mi_macomb"
PLATFORM = "mi_macomb"
BASE = "https://macombgovdocs.org/clerk/electionresults"
ELECTIONS = {
    "2021/August21": (date(2021, 8, 3), "August 3, 2021 Election"),
    "2021/November21": (date(2021, 11, 2), "November 2, 2021 Election"),
    "2022/March22": (date(2022, 3, 1), "March 1, 2022 Election"),
    "2022/May22": (date(2022, 5, 3), "May 3, 2022 Election"),
    "2022/August22": (date(2022, 8, 2), "August 2, 2022 Election"),
    "2022/November22": (date(2022, 11, 8), "November 8, 2022 Election"),
    "2023/May23": (date(2023, 5, 2), "May 2, 2023 Election"),
    "2023/August23": (date(2023, 8, 8), "August 8, 2023 Election"),
    "2023/November23": (date(2023, 11, 7), "November 7, 2023 Election"),
    "2024/January24": (date(2024, 1, 30), "January 30, 2024 Election"),
    "2024/February24": (date(2024, 2, 27), "February 27, 2024 Election"),
    "2024/April24": (date(2024, 4, 16), "April 16, 2024 Election"),
    "2024/May24": (date(2024, 5, 7), "May 7, 2024 Election"),
}
STATE = re.compile(
    r"^(?:\d+(?:st|nd|rd|th) District|Governor|Secretary of State|Attorney General|Justice of|Representative in Congress|"
    r"State (?:Senator|Representative)|Member of the State Board|Regent|Trustee of|Board of Education|"
    r"State Board|University of|Wayne State|Michigan State)"
)
TERMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


def _races(folder: str) -> list[tuple[str, str]]:
    page = raw.cached(SOURCE, f"{BASE}/{folder}/results.html").path.read_text(encoding="latin-1")
    return [
        (name, html.unescape(re.sub(r"<[^>]+>", "", title)).strip())
        for name, title in re.findall(r'HREF="(\d+)\.html" CLASS="raceitem">(.*?)</A>', page, re.IGNORECASE | re.DOTALL)
    ]


def fetch() -> None:
    for layer in enr_clarity.LAYERS:
        raw.fetch(SOURCE, enr_clarity.CENSUS.format(fips="26", layer=layer), suffix=".zip")
    for folder in ELECTIONS:
        raw.fetch(SOURCE, f"{BASE}/{folder}/results.html", suffix=".html")
        for name, title in _races(folder):
            if STATE.search(title) is None:
                raw.fetch(SOURCE, f"{BASE}/{folder}/{name}.html", suffix=".html")


def _read(registry: object) -> Iterator[dict[str, Any]]:
    for folder, (day, title) in ELECTIONS.items():
        contests: list[dict[str, Any]] = []
        for name, race in _races(folder):
            if STATE.search(race) is not None:
                continue
            page = raw.cached(SOURCE, f"{BASE}/{folder}/{name}.html").path.read_text(encoding="latin-1")
            if "Official" not in page:
                continue
            terms = re.search(r'racetitle2">\s*<br>\s*(\w+)\s', page)
            rows = re.findall(
                r'class="candname">(.*?)</td>\s*<td class="party">(.*?)</td>\s*<td class="vtotal">([\d,]+)</td>',
                page,
                re.IGNORECASE | re.DOTALL,
            )
            choices = [
                {
                    "text": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip(),
                    "party": None,
                    "total": total.replace(",", ""),
                    "places": [],
                }
                for text, _, total in rows
            ]
            if choices:
                contests.append(
                    {
                        "key": name,
                        "text": race,
                        "vote_for": TERMS.get(terms[1].lower(), 1) if terms is not None else 1,
                        "choices": choices,
                    }
                )
        yield {
            "tenant": BASE,
            "election": folder,
            "day": day,
            "title": title,
            "official": True,
            "primary": False,
            "contests": contests,
        }


def parse() -> None:
    enr_clarity.parse(SOURCE, PLATFORM, _read)


def emit() -> Path:
    return enr_clarity.emit(SOURCE, PLATFORM, lambda link, tenant: link.startswith(tenant) and link.endswith(".html"))
