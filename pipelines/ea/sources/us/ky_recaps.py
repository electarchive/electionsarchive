import re
from urllib.parse import quote, unquote

from ea import raw
from ea.store import DATA

SOURCE = "us.ky_recaps"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOST = "https://elect.ky.gov"
PAGES = {
    "2022-primary": "/results/2020-2029/Pages/2022recap-sheets.aspx",
    "2022-general": "/results/2020-2029/Pages/2022-General-Recap-Sheets.aspx",
    "2023-primary": "/results/2020-2029/Pages/2023Primary-Recap-Sheets.aspx",
    "2023-general": "/results/2020-2029/Pages/2023General-Recap-Sheets.aspx",
    "2024-primary": "/results/2020-2029/Pages/2024Primary-Recap-Sheets.aspx",
    "2024-general": "/results/2020-2029/Pages/2024General-Recap-Sheets.aspx",
    "2026-primary": "/results/2020-2029/Pages/2026Primary-Recap-Sheets.aspx",
}
COUNTIES = ("Jefferson",)


def links() -> dict[tuple[str, str], str]:
    found = {}
    for election, page in PAGES.items():
        text = raw.cached(SOURCE, HOST + page).path.read_text(errors="replace")
        for href in re.findall(r'href="([^"]*\.pdf)"', text):
            name = unquote(href).rsplit("/", 1)[1].removesuffix(".pdf").removesuffix(" County")
            if name in COUNTIES:
                found[(election, name)] = HOST + quote(unquote(href), safe="/")
    return found


def fetch() -> None:
    for page in PAGES.values():
        raw.fetch(SOURCE, HOST + page, suffix=".html")
    for url in links().values():
        raw.fetch(SOURCE, url, suffix=".pdf")
