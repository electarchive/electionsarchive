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
    1932: ("statementofvo19321939cali", date(1932, 11, 8), range(147, 150)),
    1934: ("statementofvo19321939cali", date(1934, 11, 6), range(264, 270)),
    1936: ("statementofvo19321939cali", date(1936, 11, 3), range(419, 422)),
    1938: ("statementofvo19321939cali", date(1938, 11, 8), range(517, 520)),
    1940: ("stateofcaliforn194050cali", date(1940, 11, 5), range(114, 117)),
    1942: ("stateofcaliforn194050cali", date(1942, 11, 3), range(202, 205)),
    1944: ("stateofcaliforn194050cali", date(1944, 11, 7), range(270, 273)),
    1946: ("stateofcaliforn194050cali", date(1946, 11, 5), range(348, 351)),
    1948: ("stateofcaliforn194050cali", date(1948, 11, 2), range(413, 416)),
    1950: ("stateofcaliforn194050cali", date(1950, 11, 7), range(506, 508)),
    1952: ("stateofcaliforn195262cali", date(1952, 11, 4), range(56, 59)),
    1954: ("stateofcaliforn195262cali", date(1954, 11, 2), range(131, 134)),
    1956: ("stateofcaliforn195262cali", date(1956, 11, 6), range(199, 202)),
    1958: ("stateofcaliforn195262cali", date(1958, 11, 4), range(267, 270)),
    1960: ("stateofcaliforn195262cali", date(1960, 11, 8), range(333, 336)),
    1962: ("castatem196264cali", date(1962, 11, 6), range(19, 23)),
    1964: ("castatem196264cali", date(1964, 11, 3), range(158, 161)),
    1966: ("californiastate196668cali", date(1966, 11, 8), range(55, 59)),
    1968: ("californiastate196668cali", date(1968, 11, 5), range(193, 197)),
    1972: ("statementofvote197072cali", date(1972, 11, 7), range(265, 269)),
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
ERRATA: set[tuple[int, int]] = {(1988, 4), (1952, 1)}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = True
TURNED: set[tuple[int, int]] = {(1976, page) for page in range(80, 85)}
TRANSPOSED = {1932, 1934, 1936, 1938, 1940, 1942, 1944, 1946, 1948, 1950, 1960, 1958, 1956, 1954, 1952}
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
