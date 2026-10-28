import json
from urllib.parse import quote

from ea import raw

SOURCE = "tr.ysk_mayors"
API = "https://tr.wikipedia.org/w/api.php"
YSK = "https://www.ysk.gov.tr/doc/dosyalar/docs/"
RESULTS_2004 = f"{YSK}Mahalli/2004/BelediyeBaskanligi/Pdf/2004Mahalli-BelediyeBsk-Tumu.pdf"
CANDIDATES_2009 = (
    f"{YSK}2009MahalliIdareler/2009ilceBelediyeBsk-AdayListesi.pdf",
    f"{YSK}2009MahalliIdareler/2009BeldeBelediyeBsk-AdayListesi.pdf",
)


def titles(year: int) -> list[str]:
    url = (
        f"{API}?action=query&list=search&srsearch={quote(f'intitle:\"{year} Türkiye yerel seçimleri\"')}"
        "&srlimit=200&srnamespace=0&format=json"
    )
    found = json.loads(raw.cached(SOURCE, url).path.read_bytes())["query"]["search"]
    return sorted(row["title"] for row in found)


def page(title: str) -> str:
    return f"{API}?action=parse&format=json&formatversion=2&prop=wikitext&page={quote(title)}"


def fetch() -> None:
    for year in (2004, 2009):
        search = (
            f"{API}?action=query&list=search&srsearch={quote(f'intitle:\"{year} Türkiye yerel seçimleri\"')}"
            "&srlimit=200&srnamespace=0&format=json"
        )
        raw.fetch(SOURCE, search, suffix=".json")
        for title in titles(year):
            raw.fetch(SOURCE, page(title), suffix=".json")
    raw.fetch(SOURCE, RESULTS_2004, suffix=".pdf")
    for url in CANDIDATES_2009:
        raw.fetch(SOURCE, url, suffix=".pdf")
