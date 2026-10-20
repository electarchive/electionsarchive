import sys
from datetime import date
from pathlib import Path

from ea.sources.us import house_books

SOURCE = "us.ca_house"
RAW = "us.ca_sov"
VERSION = "3"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote, General Election, {day:%B %-d, %Y}"
LICENSE = "Public record"
SUMMARY = "California's House results by county from the Secretary of State's Statement of Vote"
STATE = "CA"
FIPS = "06"
ITEMS = {
    1970: ("statementofvote197072cali", date(1970, 11, 3), range(68, 72)),
    1974: ("statementofvote197374cali", date(1974, 11, 5), range(113, 117)),
    1976: ("statementofvote1976cali", date(1976, 11, 2), range(80, 85)),
    1978: ("statementofvote197879cali", date(1978, 11, 7), range(259, 266)),
    1980: ("statementofvote41980cali", date(1980, 11, 4), range(23, 29)),
    1982: ("statementofvote21982cali", date(1982, 11, 2), range(32, 39)),
    1984: ("statementofvote61984cali", date(1984, 11, 6), range(20, 27)),
    1986: ("statementofvote41986cali", date(1986, 11, 4), range(31, 37)),
    1988: ("statementofvote81988cali", date(1988, 11, 8), range(28, 34)),
    1990: ("statementofvote61990cali", date(1990, 11, 6), range(36, 45)),
    1992: ("statementofvote31992cali", date(1992, 11, 3), range(42, 50)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
for year, day in ((1994, date(1994, 11, 8)), (1996, date(1996, 11, 5))):
    VOLUMES[year] = (
        f"https://elections.cdn.sos.ca.gov/sov/{year}-general/us-representative.pdf",
        f"https://elections.cdn.sos.ca.gov/sov/{year}-general/",
        day,
        range(1, 9),
    )
ERRATA: set[tuple[int, int]] = {(1988, 4)}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = True
TURNED: set[tuple[int, int]] = {(1976, page) for page in range(80, 85)}
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
