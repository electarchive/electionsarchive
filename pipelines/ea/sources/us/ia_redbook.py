from ea import raw

SOURCE = "us.ia_redbook"
VERSION = "1"
URL = "https://www.legis.iowa.gov/docs/shelves/redbooks/Redbook-{0}-{1}%20({2}GA).pdf"
YEARS = range(1907, 2000, 2)


def url(year: int) -> str:
    return URL.format(year, year + 1, (year - 1951) // 2 + 54)


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")
