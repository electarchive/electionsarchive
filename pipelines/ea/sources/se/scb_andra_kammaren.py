"""Sweden's Second Chamber (andra kammaren) elections of 1911 to 1968 by constituency, digitized from Statistics
Sweden's Riksdagsmannavalen volumes.

Each volume of Riksdagsmannavalen (Sveriges officiella statistik) prints a table of valid ballots by party and members
elected by party for every constituency (valkrets), usually beside the same figures for the election before. The
tables were read from the page images, which have no text layer under the figures, into the TSV files in
`scb_andra_kammaren/`, one per volume, with one row per constituency and year as printed and the national row (Hela
riket) last. Before a package is written, every row's party ballots must add up to its printed total and its party
seats to its printed seat total, and every column must add up to the national row; `locators.tsv` holds the box of
every figure on its page.

The volumes and the tables used:

- Riksdagsmannavalen åren 1909–1911, table 7: 1911.
- Riksdagsmannavalen år 1914, table 2: spring 1914 (the 1911 rows printed above it are not stored).
- Riksdagsmannavalen åren 1915–1917, table 4: 1917 and autumn 1914 (its county subtotals are not stored).
- Riksdagsmannavalen åren 1918–1920, table 6: 1920.
- Riksdagsmannavalen år 1921, table 5: 1921, each constituency's rural districts and towns apart.
- Riksdagsmannavalen åren 1925–1928, table 3: 1928 and 1924.
- Riksdagsmannavalen åren 1933–1936, table 3: 1936 and 1932.
- Riksdagsmannavalen åren 1941–1944, table 4: 1944 and 1940.
- Riksdagsmannavalen åren 1949–1952, table 3: 1952 and 1948.
- Riksdagsmannavalen åren 1957–1958, part I, table 2: 1958 and 1956.
- Riksdagsmannavalen åren 1959–1960, part I, table 2: 1960 (its 1958 rows match the 1957–1958 volume).
- Riksdagsmannavalen åren 1961–1964, part I, tables 9 and 4: 1964, from the rows for whole constituencies (hela
  valkretsen) of the table by municipality and election district, with the electorate, and the members elected.
- Riksdagsmannavalen åren 1965–1968, part 1, tables 4 and 5: 1968, with the electorate.
"""

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.se import scb, val

SOURCE = scb.SOURCE
FOLDER = Path(__file__).with_name("scb_andra_kammaren")
SERIES = (
    "https://share.scb.se/ov9993/data/historisk%20statistik/SOS%201911-%2FValstatistiken%2F"
    "Riksdagsmannavalen%20%28SOS%29%201909-1968%2FValstatistik-Riksdagsmannavalen-"
)
REGISTRY = f"{SOURCE}/andra-kammaren-registry"
GEO = f"{SOURCE}/andra-kammaren-geo"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"

VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
SWEDEN = val.SWEDEN
COUNTRY = val.COUNTRY


@dataclass(frozen=True)
class Volume:
    key: str  # the TSV in FOLDER
    file: str  # the PDF's name on share.scb.se, without the series prefix
    title: str
    table: str
    layout: dict[int, str]  # the PDF pages of the table and the columns each prints; seats* is every seat column

    @property
    def url(self) -> str:
        return f"{SERIES}{self.file}.pdf"

    @property
    def pages(self) -> list[int]:
        return list(self.layout)

    def page_columns(self, page: int) -> list[str]:
        out: list[str] = []
        for column in self.layout[page].split():
            out += seat_columns(self.key) + ["seats"] if column == "seats*" else [column]
        return out

    @property
    def source_id(self) -> str:
        return scb.key("source", "riksdagsmannavalen", self.key)


VOLUMES = {
    v.key: v
    for v in (
        Volume(
            "1909-1911",
            "1909-1910-1911",
            "Riksdagsmannavalen åren 1909–1911",
            "tab. 7",
            {159: "H LS S O total invalid seats*"},
        ),
        Volume(
            "1914-var",
            "1914",
            "Riksdagsmannavalen år 1914 (efter riksdagsupplösningen våren 1914)",
            "tab. 2",
            {
                141: "H L S O total invalid seats*",
                143: "H L S O total invalid seats*",
                145: "H L S O total invalid seats*",
                147: "H L S O total invalid seats*",
            },
        ),
        Volume(
            "1915-1917",
            "1915-1916-1917",
            "Riksdagsmannavalen åren 1915–1917",
            "tab. 4",
            {
                160: "H JR B L",
                161: "S VS O total invalid seats*",
                162: "H JR B L",
                163: "S VS O total invalid seats*",
                164: "H JR B L",
                165: "S VS O total invalid seats*",
                166: "H JR B L",
                167: "S VS O total invalid seats*",
            },
        ),
        Volume(
            "1918-1920",
            "1918-1919-1920",
            "Riksdagsmannavalen åren 1918–1920",
            "tab. 6",
            {164: "H JR B JRB LS", 165: "S VS O total invalid seats*"},
        ),
        Volume(
            "1921",
            "1921",
            "Riksdagsmannavalen år 1921",
            "tab. 5",
            {152: "H B L S", 153: "VS K O total invalid seats*"},
        ),
        Volume(
            "1925-1928",
            "1925-1926-1927-1928",
            "Riksdagsmannavalen åren 1925–1928",
            "tab. 3",
            {220: "H B L F", 221: "S KH K O total invalid seats*"},
        ),
        Volume(
            "1933-1936",
            "1933-1934-1935-1936",
            "Riksdagsmannavalen åren 1933–1936",
            "tab. 3",
            {204: "H B FP S SP", 205: "K N O total invalid seats*"},
        ),
        Volume(
            "1941-1944",
            "1941-1942-1943-1944",
            "Riksdagsmannavalen åren 1941–1944",
            "tab. 4",
            {216: "H B FP S", 217: "K O total invalid seats*"},
        ),
        Volume(
            "1949-1952",
            "1949-1950-1951-1952",
            "Riksdagsmannavalen åren 1949–1952",
            "tab. 3",
            {86: "H B FP S", 87: "K O total invalid seats*"},
        ),
        Volume(
            "1957-1958",
            "1957-1958-I",
            "Riksdagsmannavalen åren 1957–1958, I",
            "tab. 2",
            {12: "H C FP S", 13: "K O total invalid seats*"},
        ),
        Volume(
            "1959-1960",
            "1959-1960-I",
            "Riksdagsmannavalen åren 1959–1960, I",
            "tab. 2",
            {14: "H C FP S", 15: "K O total invalid seats*"},
        ),
        Volume(
            "1961-1964",
            "1961-1964-I",
            "Riksdagsmannavalen åren 1961–1964, I",
            "tab. 9 (the rows for whole constituencies) and tab. 4",
            {},
        ),
        Volume(
            "1965-1968",
            "1965-1968-1",
            "Riksdagsmannavalen åren 1965–1968, del 1: Andrakammarvalet 1968",
            "tabell 4 and 5",
            {20: "registered total invalid H C", 21: "F S68 MP KDS S K O", 22: "seats*"},
        ),
    )
}


@dataclass(frozen=True)
class Election:
    year: int
    volume: str
    start: date
    end: date
    note: str = ""
    precision: str = "day"
    season: str = ""  # "var" (spring) or "host" (autumn) in a year of two elections

    @property
    def tag(self) -> str:
        """The election's key: its year, with the season where a year had two elections."""
        return f"{self.year}-{self.season}" if self.season else str(self.year)

    @property
    def label(self) -> tuple[str, str]:
        """The year as the English and Swedish names give it."""
        english = {"var": "spring ", "host": "autumn "}.get(self.season, "")
        swedish = {"var": "våren ", "host": "hösten "}.get(self.season, "")
        return f"{english}{self.year}", f"{swedish}{self.year}"

    @property
    def day(self) -> date:
        return self.end if self.start == self.end else self.start


ELECTIONS = [
    Election(
        1911,
        "1909-1911",
        date(1911, 9, 1),
        date(1911, 9, 30),
        "The first Second Chamber election under proportional representation. The constituencies voted on different "
        "days in September: most on Sunday 3, 10, 17, or 24 September, and the town constituencies on weekdays.",
        "month",
    ),
    Election(
        1914,
        "1914-var",
        date(1914, 3, 27),
        date(1914, 4, 7),
        "An extra election after the King dissolved the Riksdag on 3 March 1914 over the defence question. Most "
        "constituencies voted on Sunday 5 April and Blekinge län on 29 March; the two Stockholm city constituencies "
        "voted first, on Friday 27 March, and Örebro län's two constituencies last, on Tuesday 7 April.",
        season="var",
    ),
    Election(
        1914,
        "1915-1917",
        date(1914, 9, 1),
        date(1914, 9, 30),
        "The autumn election of 1914. Most constituencies voted on Sunday 6 or 13 September (some towns on the "
        "Saturday before), Gotlands län on Saturday 5 September, and the town constituencies on weekdays.",
        "month",
        "host",
    ),
    Election(
        1917,
        "1915-1917",
        date(1917, 9, 1),
        date(1917, 9, 30),
        "Most constituencies voted on Sunday 2, 9, or 16 September (some towns on the Saturday or Tuesday before), "
        "Gotlands län on Saturday 1 September, and the town constituencies on weekdays.",
        "month",
    ),
    Election(
        1920,
        "1918-1920",
        date(1920, 9, 1),
        date(1920, 9, 30),
        "Most constituencies voted on Sunday 5 or 12 September, Gotlands län on Saturday 4 September, and the town "
        "constituencies on weekdays early in the month. The elections in Västerbottens läns norra and Malmöhus läns "
        "södra valkrets were held again on 12 and 19 December 1920 (omval).",
        "month",
    ),
    Election(
        1921,
        "1921",
        date(1921, 9, 10),
        date(1921, 9, 26),
        "The first Second Chamber election with universal suffrage for women and men. Most constituencies voted on "
        "Sunday 11 or 18 September (some towns on the Saturday before); the Fyrstadskretsen constituency voted on 10 "
        "September, Stockholms stad and Göteborgs stad on 16 September, and Norrbottens län on Monday 26 September.",
    ),
    Election(
        1924,
        "1925-1928",
        date(1924, 9, 19),
        date(1924, 9, 21),
        "Voting took place on Sunday 21 September in 25 constituencies (in some towns the day before), and on "
        "Friday 19 September in Stockholms stad and Göteborgs stad and on the Saturday in the Malmö constituency.",
    ),
    Election(
        1928,
        "1925-1928",
        date(1928, 9, 15),
        date(1928, 9, 21),
        "Voting took place on Sunday 16 September in most of the country, on Saturday 15 September in the "
        "Fyrstadskretsen and Göteborgs stad constituencies and in a number of towns, and on Friday 21 September in "
        "Stockholms stad.",
    ),
    Election(
        1932,
        "1933-1936",
        date(1932, 9, 17),
        date(1932, 9, 20),
        "Sunday 18 September was the general polling day; Göteborg, the Fyrstadskretsen constituency, and a number "
        "of towns voted on Saturday 17 September, and Stockholms stad on Tuesday 20 September.",
    ),
    Election(1936, "1933-1936", date(1936, 9, 20), date(1936, 9, 20)),
    Election(1940, "1941-1944", date(1940, 9, 15), date(1940, 9, 15)),
    Election(1944, "1941-1944", date(1944, 9, 17), date(1944, 9, 17)),
    Election(1948, "1949-1952", date(1948, 9, 19), date(1948, 9, 19)),
    Election(1952, "1949-1952", date(1952, 9, 21), date(1952, 9, 21)),
    Election(1956, "1957-1958", date(1956, 9, 16), date(1956, 9, 16)),
    Election(
        1958,
        "1957-1958",
        date(1958, 6, 1),
        date(1958, 6, 1),
        "An extra election, held after the Second Chamber was dissolved on 28 April 1958.",
    ),
    Election(1960, "1959-1960", date(1960, 9, 18), date(1960, 9, 18)),
    Election(1964, "1961-1964", date(1964, 9, 20), date(1964, 9, 20)),
    Election(1968, "1965-1968", date(1968, 9, 15), date(1968, 9, 15)),
]
BY_TAG = {e.tag: e for e in ELECTIONS}


# ---------------------------------------------------------------------------------------------------------------------
# Constituencies. A constituency that was a whole county (or, for Stockholm, the city or the county around it) is
# that county's area; the six others are this pipeline's own constituency areas.


@dataclass(frozen=True)
class Krets:
    name: str
    scheme: str
    code: str

    @property
    def area(self) -> str:
        if self.scheme == val.CONSTITUENCY:
            return scb.key("area", "andra-kammaren", self.code)
        return scb.area(self.scheme, self.code)


# The 56 constituencies of 1911 to 1920, in the tables' order. Göteborgs stad kept its constituency in 1921.
KRETSAR_1911 = {
    "stockholms-stad-1": Krets("Stockholms stads första valkrets", val.CONSTITUENCY, "AK1911-stockholms-stad-1"),
    "stockholms-stad-2": Krets("Stockholms stads andra valkrets", val.CONSTITUENCY, "AK1911-stockholms-stad-2"),
    "stockholms-lan-sodra": Krets("Stockholms läns södra valkrets", val.CONSTITUENCY, "AK1911-stockholms-lan-sodra"),
    "stockholms-lan-norra": Krets("Stockholms läns norra valkrets", val.CONSTITUENCY, "AK1911-stockholms-lan-norra"),
    "uppsala-lan": Krets("Uppsala läns valkrets", val.COUNTY, "03"),
    "sodermanland-norra": Krets("Södermanlands läns norra valkrets", val.CONSTITUENCY, "AK1911-sodermanland-norra"),
    "sodermanland-sodra": Krets("Södermanlands läns södra valkrets", val.CONSTITUENCY, "AK1911-sodermanland-sodra"),
    "ostergotland-norra": Krets("Östergötlands läns norra valkrets", val.CONSTITUENCY, "AK1911-ostergotland-norra"),
    "norrkoping-linkoping": Krets(
        "Norrköpings och Linköpings valkrets", val.CONSTITUENCY, "AK1911-norrkoping-linkoping"
    ),
    "ostergotland-sodra": Krets("Östergötlands läns södra valkrets", val.CONSTITUENCY, "AK1911-ostergotland-sodra"),
    "jonkoping-ostra": Krets("Jönköpings läns östra valkrets", val.CONSTITUENCY, "AK1911-jonkoping-ostra"),
    "jonkoping-vastra": Krets("Jönköpings läns västra valkrets", val.CONSTITUENCY, "AK1911-jonkoping-vastra"),
    "kronoberg-ostra": Krets("Kronobergs läns östra valkrets", val.CONSTITUENCY, "AK1911-kronoberg-ostra"),
    "kronoberg-vastra": Krets("Kronobergs läns västra valkrets", val.CONSTITUENCY, "AK1911-kronoberg-vastra"),
    "kalmar-norra": Krets("Kalmar läns norra valkrets", val.CONSTITUENCY, "AK1911-kalmar-norra"),
    "kalmar-sodra": Krets("Kalmar läns södra valkrets", val.CONSTITUENCY, "AK1911-kalmar-sodra"),
    "gotland-lan": Krets("Gotlands läns valkrets", val.COUNTY, "09"),
    "blekinge-lan": Krets("Blekinge läns valkrets", val.COUNTY, "10"),
    "kristianstad-nordvastra": Krets(
        "Kristianstads läns nordvästra valkrets", val.CONSTITUENCY, "AK1911-kristianstad-nordvastra"
    ),
    "kristianstad-sydostra": Krets(
        "Kristianstads läns sydöstra valkrets", val.CONSTITUENCY, "AK1911-kristianstad-sydostra"
    ),
    "malmohus-norra": Krets("Malmöhus läns norra valkrets", val.CONSTITUENCY, "AK1911-malmohus-norra"),
    "malmohus-mellersta": Krets("Malmöhus läns mellersta valkrets", val.CONSTITUENCY, "AK1911-malmohus-mellersta"),
    "malmohus-sodra": Krets("Malmöhus läns södra valkrets", val.CONSTITUENCY, "AK1911-malmohus-sodra"),
    "halsingborg-landskrona-lund": Krets(
        "Hälsingborgs, Landskronas och Lunds valkrets", val.CONSTITUENCY, "AK1911-halsingborg-landskrona-lund"
    ),
    "malmo-stad": Krets("Malmö stads valkrets", val.CONSTITUENCY, "AK1911-malmo-stad"),
    "halland-lan": Krets("Hallands läns valkrets", val.COUNTY, "13"),
    "goteborg-bohus-sodra": Krets(
        "Göteborgs och Bohus läns södra valkrets", val.CONSTITUENCY, "AK1911-goteborg-bohus-sodra"
    ),
    "goteborg-bohus-norra": Krets(
        "Göteborgs och Bohus läns norra valkrets", val.CONSTITUENCY, "AK1911-goteborg-bohus-norra"
    ),
    "goteborgs-stad": Krets("Göteborgs stads valkrets", val.CONSTITUENCY, "AK-goteborgs-stad"),
    "alvsborg-norra-1911": Krets("Älvsborgs läns norra valkrets", val.CONSTITUENCY, "AK1911-alvsborg-norra-1911"),
    "alvsborg-mellersta": Krets("Älvsborgs läns mellersta valkrets", val.CONSTITUENCY, "AK1911-alvsborg-mellersta"),
    "alvsborg-sodra-1911": Krets("Älvsborgs läns södra valkrets", val.CONSTITUENCY, "AK1911-alvsborg-sodra-1911"),
    "skaraborg-norra": Krets("Skaraborgs läns norra valkrets", val.CONSTITUENCY, "AK1911-skaraborg-norra"),
    "skaraborg-sodra": Krets("Skaraborgs läns södra valkrets", val.CONSTITUENCY, "AK1911-skaraborg-sodra"),
    "varmland-norra": Krets("Värmlands läns norra valkrets", val.CONSTITUENCY, "AK1911-varmland-norra"),
    "varmland-ostra": Krets("Värmlands läns östra valkrets", val.CONSTITUENCY, "AK1911-varmland-ostra"),
    "varmland-vastra": Krets("Värmlands läns västra valkrets", val.CONSTITUENCY, "AK1911-varmland-vastra"),
    "orebro-norra": Krets("Örebro läns norra valkrets", val.CONSTITUENCY, "AK1911-orebro-norra"),
    "orebro-sodra": Krets("Örebro läns södra valkrets", val.CONSTITUENCY, "AK1911-orebro-sodra"),
    "vastmanland-ostra": Krets("Västmanlands läns östra valkrets", val.CONSTITUENCY, "AK1911-vastmanland-ostra"),
    "vastmanland-vastra": Krets("Västmanlands läns västra valkrets", val.CONSTITUENCY, "AK1911-vastmanland-vastra"),
    "kopparberg-ostra": Krets("Kopparbergs läns östra valkrets", val.CONSTITUENCY, "AK1911-kopparberg-ostra"),
    "kopparberg-vastra": Krets("Kopparbergs läns västra valkrets", val.CONSTITUENCY, "AK1911-kopparberg-vastra"),
    "kopparberg-norra": Krets("Kopparbergs läns norra valkrets", val.CONSTITUENCY, "AK1911-kopparberg-norra"),
    "gastrikland": Krets("Gästriklands valkrets", val.CONSTITUENCY, "AK1911-gastrikland"),
    "halsingland-sodra": Krets("Hälsinglands södra valkrets", val.CONSTITUENCY, "AK1911-halsingland-sodra"),
    "halsingland-norra": Krets("Hälsinglands norra valkrets", val.CONSTITUENCY, "AK1911-halsingland-norra"),
    "medelpad": Krets("Medelpads valkrets", val.CONSTITUENCY, "AK1911-medelpad"),
    "angermanland-sodra": Krets("Ångermanlands södra valkrets", val.CONSTITUENCY, "AK1911-angermanland-sodra"),
    "angermanland-norra": Krets("Ångermanlands norra valkrets", val.CONSTITUENCY, "AK1911-angermanland-norra"),
    "jamtland-sodra": Krets("Jämtlands läns södra valkrets", val.CONSTITUENCY, "AK1911-jamtland-sodra"),
    "jamtland-norra": Krets("Jämtlands läns norra valkrets", val.CONSTITUENCY, "AK1911-jamtland-norra"),
    "vasterbotten-sodra": Krets("Västerbottens läns södra valkrets", val.CONSTITUENCY, "AK1911-vasterbotten-sodra"),
    "vasterbotten-norra": Krets("Västerbottens läns norra valkrets", val.CONSTITUENCY, "AK1911-vasterbotten-norra"),
    "norrbotten-sodra": Krets("Norrbottens läns södra valkrets", val.CONSTITUENCY, "AK1911-norrbotten-sodra"),
    "norrbotten-norra": Krets("Norrbottens läns norra valkrets", val.CONSTITUENCY, "AK1911-norrbotten-norra"),
}

# The 28 constituencies of 1921 to 1970.
KRETSAR_1921 = {
    "stockholms-stad": Krets("Stockholms stads valkrets", val.COUNTY, scb.STOCKHOLM_CITY),
    "stockholms-lan": Krets("Stockholms läns valkrets", val.COUNTY, scb.STOCKHOLM_COUNTY),
    "uppsala": Krets("Uppsala läns valkrets", val.COUNTY, "03"),
    "sodermanland": Krets("Södermanlands läns valkrets", val.COUNTY, "04"),
    "ostergotland": Krets("Östergötlands läns valkrets", val.COUNTY, "05"),
    "jonkoping": Krets("Jönköpings läns valkrets", val.COUNTY, "06"),
    "kronoberg": Krets("Kronobergs läns valkrets", val.COUNTY, "07"),
    "kalmar": Krets("Kalmar läns valkrets", val.COUNTY, "08"),
    "gotland": Krets("Gotlands läns valkrets", val.COUNTY, "09"),
    "blekinge": Krets("Blekinge läns valkrets", val.COUNTY, "10"),
    "kristianstad": Krets("Kristianstads läns valkrets", val.COUNTY, "11"),
    "fyrstadskretsen": Krets(
        "Malmö, Hälsingborgs, Landskrona och Lunds städers valkrets (Fyrstadskretsen)",
        val.CONSTITUENCY,
        "AK-fyrstadskretsen",
    ),
    "malmohus": Krets("Malmöhus läns valkrets", val.CONSTITUENCY, "AK-malmohus"),
    "halland": Krets("Hallands läns valkrets", val.COUNTY, "13"),
    "goteborgs-stad": Krets("Göteborgs stads valkrets", val.CONSTITUENCY, "AK-goteborgs-stad"),
    "goteborg-bohus": Krets(
        "Göteborgs och Bohus läns landstingsområdes valkrets", val.CONSTITUENCY, "AK-goteborg-bohus"
    ),
    "alvsborg-norra": Krets("Älvsborgs läns norra valkrets", val.CONSTITUENCY, "AK-alvsborg-norra"),
    "alvsborg-sodra": Krets("Älvsborgs läns södra valkrets", val.CONSTITUENCY, "AK-alvsborg-sodra"),
    "skaraborg": Krets("Skaraborgs läns valkrets", val.COUNTY, "16"),
    "varmland": Krets("Värmlands läns valkrets", val.COUNTY, "17"),
    "orebro": Krets("Örebro läns valkrets", val.COUNTY, "18"),
    "vastmanland": Krets("Västmanlands läns valkrets", val.COUNTY, "19"),
    "kopparberg": Krets("Kopparbergs läns valkrets", val.COUNTY, "20"),
    "gavleborg": Krets("Gävleborgs läns valkrets", val.COUNTY, "21"),
    "vasternorrland": Krets("Västernorrlands läns valkrets", val.COUNTY, "22"),
    "jamtland": Krets("Jämtlands läns valkrets", val.COUNTY, "23"),
    "vasterbotten": Krets("Västerbottens läns valkrets", val.COUNTY, "24"),
    "norrbotten": Krets("Norrbottens läns valkrets", val.COUNTY, "25"),
}
KRETSAR = KRETSAR_1911 | KRETSAR_1921


def kretsar(year: int) -> dict[str, Krets]:
    return KRETSAR_1911 if year < 1921 else KRETSAR_1921


def valid(krets: Krets) -> tuple[date, date]:
    """When an own constituency area existed: the 1911 ones from the electoral law of 1909 until 1921, the 1921 ones
    until the unicameral Riksdag; Göteborgs stad throughout."""
    if krets.code == "AK-goteborgs-stad":
        return date(1909, 1, 1), date(1971, 1, 1)
    if krets.code.startswith("AK1911-"):
        return date(1909, 1, 1), date(1921, 1, 1)
    return date(1921, 1, 1), date(1971, 1, 1)


OWN_NOTES = {
    "AK-fyrstadskretsen": "The towns of Malmö, Hälsingborg (Helsingborg), Landskrona, and Lund, which formed a "
    "Second Chamber constituency of their own.",
    "AK-malmohus": "Malmöhus län without the four towns of the Fyrstadskretsen constituency.",
    "AK-goteborgs-stad": "The city of Göteborg, a Second Chamber constituency of its own from 1911.",
    "AK-goteborg-bohus": "Göteborgs och Bohus län without the city of Göteborg: the area of the county council "
    "(landstingsområde).",
    "AK-alvsborg-norra": "The northern constituency of Älvsborgs län.",
    "AK-alvsborg-sodra": "The southern constituency of Älvsborgs län.",
}


# ---------------------------------------------------------------------------------------------------------------------
# Parties. Each column of a table is a party, a joint designation, or the others; the party a column stands for can
# change from one election to the next.


@dataclass(frozen=True)
class Party:
    name: str
    abbreviation: str
    kind: str = "party"
    english: str | None = None
    historical: tuple[tuple[str, date | None, date | None], ...] = ()


PARTIES = {
    "H": Party(
        "Högerpartiet",
        "H",
        english="Right Party",
        historical=(("Allmänna valmansförbundet", None, date(1938, 1, 1)), ("Högern", None, date(1952, 1, 1))),
    ),
    "C": Party(
        "Centerpartiet",
        "C",
        english="Centre Party",
        historical=(("Bondeförbundet", None, date(1957, 6, 1)),),
    ),
    "LS": Party("Liberala samlingspartiet", "L", english="Liberal Coalition Party"),
    "JR": Party("Jordbrukarnas riksförbund", "JR", english="Farmers' National Union"),
    "JRB": Party(
        "Jordbrukarnas riksförbund och Bondeförbundet",
        "JR+B",
        kind="bloc",
        english="Farmers' National Union and Farmers' League",
    ),
    "L": Party("Sveriges liberala parti", "L", english="Liberal Party of Sweden"),
    "F": Party("Frisinnade folkpartiet", "F", english="Free-minded People's Party"),
    "LF": Party(
        "Frisinnade folkpartiet och Sveriges liberala parti",
        "L+F",
        kind="bloc",
        english="Free-minded and Liberal parties",
    ),
    "FP": Party("Folkpartiet", "FP", english="People's Party"),
    "S": Party("Sveriges socialdemokratiska arbetareparti", "S", english="Social Democrats"),
    "VS": Party("Sveriges socialdemokratiska vänsterparti", "VS", english="Left Social Democrats"),
    "KH": Party("Sveriges kommunistiska parti (Höglundsriktningen)", "K (Höglund)", english="Höglund Communists"),
    "K": Party("Sveriges kommunistiska parti", "K", english="Communist Party of Sweden"),
    "SP": Party(
        "Socialistiska partiet",
        "SP",
        english="Socialist Party",
        historical=(("Sveriges kommunistiska parti (Kilbomsriktningen)", None, date(1934, 1, 1)),),
    ),
    "N": Party("Sveriges nationella förbund", "N", english="National League of Sweden"),
    "KDS": Party("Kristen demokratisk samling", "KDS", english="Christian Democratic Unity"),
    "MbS": Party(
        "Medborgerlig samling",
        "MbS",
        kind="bloc",
        english="Civic Unity (Right, Centre, and People's parties)",
    ),
    "S68": Party("Samling 68", "S 68", kind="bloc", english="Samling 68 (Right, Centre, and People's parties)"),
    "MP": Party("Mittenpartierna", "Mp", kind="bloc", english="Centre parties (Centre and People's parties)"),
}

# The party behind each column, where it differs from the column's own name.
COLUMN_PARTY: dict[tuple[int, str], str] = {
    (1914, "B"): "C",
    (1914, "L"): "LS",
    (1917, "B"): "C",
    (1917, "L"): "LS",
    (1920, "B"): "C",
    (1921, "B"): "C",
    (1921, "L"): "LS",
    (1924, "B"): "C",
    (1928, "B"): "C",
    (1932, "B"): "C",
    (1936, "B"): "C",
    (1940, "B"): "C",
    (1944, "B"): "C",
    (1948, "B"): "C",
    (1952, "B"): "C",
    (1932, "FP"): "LF",
}
LABELS: dict[str, str] = {
    "H": "Högern",
    "B": "Bondeförbundet",
    "C": "Centerpartiet",
    "L": "Liberala",
    "F": "Frisinnade",
    "FP": "Folkpartiet",
    "S": "Socialdemokrater",
    "VS": "Vänstersocialister",
    "LS": "Liberala",
    "JR": "Jordbrukarnas riksförbund",
    "JRB": "Jordbrukarnas riksförbund och bondeförbundet",
    "KH": "Kommunister (Höglundsriktningen)",
    "K": "Kommunister",
    "SP": "Socialistiska partiet",
    "N": "Nationella",
    "KDS": "Kristen demokratisk samling",
    "S68": "Samling 68",
    "MbS": "Medborgerlig samling",
    "MP": "Mittenpartierna",
    "O": "Övriga",
}
YEAR_LABELS: dict[tuple[int, str], str] = {
    (1911, "H"): "Moderata",
    (1914, "H"): "Moderata",
    (1917, "H"): "Moderata",
    (1924, "K"): "Kommunister (Kilbomsriktningen)",
    (1932, "FP"): "Liberala och frisinnade",
    (1932, "SP"): "Kilbomskommunister",
    (1932, "K"): "Sillénkommunister",
    (1960, "H"): "Högerpartiet",
    (1964, "H"): "Högerpartiet",
    (1968, "H"): "Högerpartiet",
}


def party(key: str) -> str:
    return scb.key("party", "andra-kammaren", key)


# ---------------------------------------------------------------------------------------------------------------------
# Reading the tables.

type Row = dict[str, int | None]


def _number(text: str) -> int | None:
    return None if text in ("-", "—", "") else int(text)


def rows(volume: str) -> list[tuple[int, str, Row]]:
    path = FOLDER / f"{volume}.tsv"
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader((line for line in f if not line.startswith("#")), delimiter="\t")
        return [
            (int(r["year"]), r["krets"], {k: _number(v) for k, v in r.items() if k not in ("year", "krets")})
            for r in reader
        ]


def columns(volume: str) -> list[str]:
    path = FOLDER / f"{volume}.tsv"
    with path.open(encoding="utf-8") as f:
        header = next(line for line in f if not line.startswith("#"))
    return header.rstrip("\n").split("\t")[2:]


def party_columns(volume: str) -> list[str]:
    cols = columns(volume)
    return cols[: cols.index("total")]


def seat_columns(volume: str) -> list[str]:
    return [c for c in columns(volume) if c.startswith("seats:")]


# Rows whose printed party ballots do not add up to their printed total, by the difference (total minus sum). The
# figures were checked against the page; the national row of the same part repeats the difference.
PRINTED: dict[tuple[str, int, str], int] = {
    ("1921", 1921, "kristianstad/lb"): 1,
    ("1921", 1921, "kristianstad/st"): -1,
    ("1921", 1921, "riket/lb"): 1,
    ("1921", 1921, "riket/st"): -1,
}


# Columns a national row is not checked against, because a constituency's figure is illegible and not stored.
ILLEGIBLE = {("1961-1964", 1964, "registered")}

# Joint ballots of two parties under one designation, which the national row divides between the parties.
JOINT = {"JRB": ("JR", "B")}


def base(krets: str) -> str:
    """The constituency of a row: kalmar/lb (the rural districts of Kalmar läns valkrets) is kalmar."""
    return krets.split("/")[0]


def check(volume: str) -> None:
    """Every row adds up to its printed totals, and every column to the printed national row (and a part's column,
    where a table prints the rural districts and towns apart, to the national row of that part)."""
    found = rows(volume)
    parties, seats = party_columns(volume), seat_columns(volume)
    for year, krets, row in found:
        if sum(row[c] or 0 for c in parties) + PRINTED.get((volume, year, krets), 0) != row["total"]:
            raise ValueError(f"{volume} {year} {krets}: the party ballots do not add up to the printed total")
        if sum(row[c] or 0 for c in seats) != (row["seats"] or 0):
            raise ValueError(f"{volume} {year} {krets}: the party seats do not add up to the printed total")
        if base(krets) != "riket" and base(krets) not in kretsar(year):
            raise ValueError(f"{volume}: unknown constituency {krets}")
    for year in sorted({y for y, _, _ in found}):
        units = [(k, row) for y, k, row in found if y == year and base(k) != "riket"]
        if [base(k) for k, _ in units if not k.endswith("/st")] != list(kretsar(year)):
            raise ValueError(f"{volume} {year}: expected the {len(kretsar(year))} constituencies in order")
        for total in (k for y, k, _ in found if y == year and base(k) == "riket"):
            part = total.partition("/")[2]
            national = next(row for y, k, row in found if y == year and k == total)
            members = [row for k, row in units if not part or k.endswith(f"/{part}") or (part == "st" and "/" not in k)]
            for column in columns(volume):
                if column in JOINT or any(column in parts for parts in JOINT.values()):
                    continue
                if (volume, year, column) in ILLEGIBLE:
                    continue
                if sum(row[column] or 0 for row in members) != (national[column] or 0):
                    raise ValueError(f"{volume} {year}: column {column} does not add up to the {total} row")
            for joint, parts in JOINT.items():
                if joint in columns(volume):
                    mine = sum(row[c] or 0 for row in members for c in (joint, *parts))
                    if mine != sum(national[c] or 0 for c in parts):
                        raise ValueError(f"{volume} {year}: {joint} and its parties do not add up to the {total} row")


def page_sizes() -> dict[tuple[str, int], tuple[int, int]]:
    """(volume, page) -> (width, height) of the page in pixels at 100 dpi."""
    with (FOLDER / "pages.tsv").open(encoding="utf-8") as f:
        return {
            (r["volume"], int(r["page"])): (int(r["width"]), int(r["height"]))
            for r in csv.DictReader(f, delimiter="\t")
        }


def locators() -> dict[tuple[str, int, str, str], tuple[int, float, float, float, float]]:
    """(volume, year, krets, column) -> (page, x0, y0, x1, y1), in pixels of the page at 100 dpi."""
    out: dict[tuple[str, int, str, str], tuple[int, float, float, float, float]] = {}
    with (FOLDER / "locators.tsv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            out[(r["volume"], int(r["year"]), r["krets"], r["column"])] = (
                int(r["page"]),
                float(r["x0"]),
                float(r["y0"]),
                float(r["x1"]),
                float(r["y1"]),
            )
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Packages.


def fetch() -> None:
    for v in VOLUMES.values():
        raw.fetch(SOURCE, v.url, suffix=".pdf")


def packages() -> list[str]:
    return [REGISTRY, GEO, *(election_key(e) for e in ELECTIONS)]


def election_key(e: Election) -> str:
    return f"{SOURCE}/andra-kammaren-{e.tag}"


def parse() -> None:
    for volume in VOLUMES:
        check(volume)
    registry().write()
    geo().write()
    for e in ELECTIONS:
        election(e).write()


BODY = scb.key("body", "andra-kammaren")
SYSTEMS: dict[str, tuple[date, date | None, dict[str, object], str]] = {
    "andra-kammaren-1909": (
        date(1909, 1, 1),
        date(1952, 1, 1),
        {"formula": "dhondt"},
        (
            "Each constituency elects its members by party-list proportional representation, allocated by the d'Hondt "
            "method. Until 1948, parties could combine their lists under a joint party designation (kartell), and the "
            "seats were first divided among the joint designations and then within them."
        ),
    ),
    "andra-kammaren-1952": (
        date(1952, 1, 1),
        date(1971, 1, 1),
        {"formula": "modified_sainte_lague", "firstDivisor": 1.4},
        (
            "Each constituency elects its members by party-list proportional representation, allocated by the modified "
            "Sainte-Laguë method (jämkade uddatalsmetoden) with a first divisor of 1.4."
        ),
    ),
}


def system(year: int) -> str:
    return scb.key("electoral_system", "andra-kammaren-1952" if year >= 1952 else "andra-kammaren-1909")


def _sources(p: Package) -> None:
    for v in VOLUMES.values():
        p.add(
            "source",
            id=v.source_id,
            kind="official_body",
            publisher="Statistics Sweden (Statistiska centralbyrån)",
            title=f"Sveriges officiella statistik: {v.title}",
            url=v.url,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution="Statistics Sweden (SCB)",
        )
        p.artifact(raw.cached(SOURCE, v.url), v.source_id)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Second Chamber of the bicameral Riksdag, its electoral systems, the parties of its elections of 1911 to "
        "1968, and the Riksdagsmannavalen volumes they are digitized from",
    )
    p.external.add(SWEDEN)
    _sources(p)
    p.add("body", id=BODY, polity_id=SWEDEN, kind="lower_house")
    p.name(BODY, "Riksdagens andra kammare", "sv", "official")
    p.name(BODY, "Second Chamber of the Riksdag", "en", "official")
    p.add(
        "note",
        id=scb.key("note", BODY),
        entity_id=BODY,
        kind="context",
        body_md="The directly elected lower house of the bicameral Riksdag under the Riksdag Act of 1866. It sat from "
        "1867 until the unicameral Riksdag replaced both chambers on 1 January 1971.",
        lang="en",
    )
    for name, (since, until, rules, method) in SYSTEMS.items():
        id = scb.key("electoral_system", name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=SWEDEN,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=scb.key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for key, x in PARTIES.items():
        id = party(key)
        p.add("party", id=id, kind=x.kind, polity_id=SWEDEN)
        p.name(id, x.name, "sv", "official")
        p.name(id, x.abbreviation, "sv", "abbreviation", False)
        if x.english:
            p.name(id, x.english, "en", "official")
        for text, since, until in x.historical:
            p.add(
                "name",
                id=p.id("name", id, "sv", "historical", text),
                entity_id=id,
                text=text,
                lang="sv",
                kind="historical",
                valid_from=since,
                valid_to=until,
                is_preferred=False,
            )
    return p


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The Second Chamber constituencies of 1911 to 1970 that were not a whole county",
        depends=[f"{val.SOURCE}/geo"],
    )
    p.external |= {SWEDEN, COUNTRY, val.CONSTITUENCY}
    for krets in KRETSAR.values():
        if krets.scheme != val.CONSTITUENCY:
            continue
        id = krets.area
        since, until = valid(krets)
        p.add("area", id=id, scheme_code=val.CONSTITUENCY)
        p.add("area_code", area_id=id, code=krets.code, valid_from=since, valid_to=until)
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=since, valid_to=until)
        p.name(id, krets.name, "sv", "official")
        p.add(
            "note",
            id=scb.key("note", id),
            entity_id=id,
            kind="context",
            body_md=OWN_NOTES.get(
                krets.code,
                "A Second Chamber constituency under the electoral law of 1909, used in the elections of 1911 to 1920.",
            ),
            lang="en",
        )
    return p


METHOD = (
    "Valid ballots by party and members elected by party in each constituency, as printed in {table} of "
    "Statistics Sweden's {title} (Sveriges officiella statistik), read from the page images; every figure carries "
    "its page and box. Every constituency's party ballots add up to its printed total, its party seats to its "
    "printed seat total, and every column adds up to the printed national total. Valid ballots are the printed "
    "total; {invalid} Ballots cast are the valid ballots plus the invalid ones. Where parties went to the polls "
    "under a joint party designation (kartell), the table counts the joint ballots under the party it names, and "
    "its footnotes list the cases; a party column with a dash is not stored."
)
INVALID_TEXT = (
    "invalid ballots are the envelopes without accepted contents (valkuvert utan godkänt innehåll), which the table "
    "prints beside them."
)
INVALID_1964 = (
    "invalid ballots are the invalid ballots (ogiltiga valsedlar), and the electorate is the persons entitled to "
    "vote (röstberättigade)."
)
NOTE_1964 = (
    " The members elected on the joint lists of Medborgerlig samling (the Right, Centre, and People's parties in the "
    "Fyrstadskretsen) and Mittenpartierna (the Centre and People's parties in Gotland) are counted for those lists; "
    "table 4 counts them by their own party. The electorate of Hallands läns valkrets is illegible in the scan and "
    "not stored, and that of Västernorrlands läns valkrets is stored as printed (189,630), though the table's own "
    "percentages for it imply about 188,930; with the printed figures, the national electorate leaves 117,401 for "
    "Halland."
)
INVALID_1968 = (
    "invalid ballots are the voters with invalid ballots (röstande med ogiltiga valsedlar), and the electorate is "
    "the persons entitled to vote (röstberättigade) in table 4."
)


PARTS = (
    " The table prints each constituency's rural districts (landsbygd) and towns (städer) apart, and each is a "
    "reporting unit within the constituency; the seats are the table's own split by where the members lived, summed "
    "for the constituency."
)
PART_NAMES = {"lb": "landsbygd", "st": "städer"}


@dataclass
class Build:
    p: Package
    e: Election
    values: list[tuple[str, str, str | None, int, int, int]] = field(default_factory=list)
    boxes: list[tuple[str, str, str | None, int, str, float, float, float, float]] = field(default_factory=list)
    pages: set[int] = field(default_factory=set)


def page_id(volume: Volume, page: int) -> str:
    return scb.key("source_page", volume.url, page)


def election(e: Election) -> Package:
    volume = VOLUMES[e.volume]
    p = Package(
        election_key(e),
        SOURCE,
        f"The Second Chamber election of {e.label[0]} by constituency, digitized from Statistics Sweden's "
        f"{volume.title}",
        depends=[REGISTRY, GEO, f"{SOURCE}/geo", f"{val.SOURCE}/geo"],
    )
    p.external |= {SWEDEN, COUNTRY, val.COUNTY, val.CONSTITUENCY}
    p.uses(raw.cached(SOURCE, volume.url))
    election_id = scb.key("election", "andra-kammaren", e.tag)
    p.add(
        "election",
        id=election_id,
        polity_id=SWEDEN,
        kind="general",
        date_from=e.start,
        date_to=e.end,
        date_precision=e.precision,
        status="held",
    )
    english, swedish = e.label
    p.name(
        election_id,
        f"Swedish Second Chamber election of {english}" if e.season else f"{e.year} Swedish Second Chamber election",
        "en",
        "official",
    )
    p.name(election_id, f"Andrakammarvalet {swedish}", "sv", "native", False)
    slug = f"se/{e.day}-andra-kammaren"
    p.add("slug", path=slug, entity_id=election_id, is_current=True)
    if e.note:
        p.add("note", id=scb.key("note", election_id), entity_id=election_id, kind="context", body_md=e.note, lang="en")
    boxes = locators()
    b = Build(p, e)
    parties = party_columns(e.volume)
    found = [(k, row) for y, k, row in rows(e.volume) if y == e.year and base(k) != "riket"]
    method = METHOD.format(
        table=volume.table,
        title=volume.title,
        invalid=INVALID_1968 if e.year == 1968 else INVALID_1964 if e.year == 1964 else INVALID_TEXT,
    )
    if e.year == 1964:
        method += NOTE_1964
    if any("/" in k for k, _ in found):
        method += PARTS
    groups: dict[str, list[tuple[str, Row]]] = {}
    for key, row in found:
        groups.setdefault(base(key), []).append((key, row))
    for krets_key, members in groups.items():
        krets = KRETSAR[krets_key]
        contest = scb.key("contest", "andra-kammaren", e.tag, krets_key)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=BODY,
            district_area_id=krets.area,
            magnitude=sum(row["seats"] or 0 for _, row in members),
            round_number=1,
            electoral_system_id=system(e.year),
            default_result_kind="counted",
        )
        p.name(contest, krets.name, "sv", "official")
        p.add("slug", path=f"{slug}/{krets_key}", entity_id=contest, is_current=True)
        result_set = scb.key("result_set", "andra-kammaren", e.tag, krets_key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=volume.source_id,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note", id=scb.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
        )
        options: dict[str, str] = {}
        for position, column in enumerate(parties, start=1):
            votes = [row[column] for _, row in members if row[column] is not None]
            won = sum(row.get(f"seats:{column}") or 0 for _, row in members)
            if not votes and not won:
                continue
            option = options[column] = scb.key("option", "andra-kammaren", e.tag, krets_key, column)
            label = YEAR_LABELS.get((e.year, column), LABELS[column])
            if e.year >= 1957 and column == "C":
                label = "Centerpartiet"
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if column == "O" else "list",
                party_id=None if column == "O" else party(COLUMN_PARTY.get((e.year, column), column)),
                ballot_position=position,
                ballot_label=label,
            )
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
        for key, row in members:
            part = key.partition("/")[2]
            unit = scb.key("unit", "andra-kammaren", e.tag, key)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=SWEDEN,
                scheme_code=krets.scheme,
                code=f"{e.day}:ak:{key}",
                area_relation="within" if part else "equals",
                area_id=krets.area,
                kind="standard",
            )
            p.name(unit, f"{krets.name}, {PART_NAMES[part]}" if part else krets.name, "sv", "official")

            def put(option: str | None, measure: int, value: int, column: str | None) -> None:
                flags = 0
                if column is not None:
                    flags = DIGITIZED
                if column is not None and (e.volume, e.year, key, column) in boxes:
                    page, x0, y0, x1, y1 = boxes[(e.volume, e.year, key, column)]
                    b.pages.add(page)
                    b.boxes.append((result_set, unit, option, measure, page_id(volume, page), x0, y0, x1, y1))
                b.values.append((result_set, unit, option, measure, value, flags))

            for column, option in options.items():
                if (votes := row[column]) is not None:
                    put(option, VOTES, votes, column)
            total, invalid = row["total"] or 0, row["invalid"] or 0
            put(None, VALID, total, "total")
            put(None, INVALID, invalid, "invalid")
            put(None, CAST, total + invalid, None)
            if (registered := row.get("registered")) is not None:
                put(None, REGISTERED, registered, "registered")
            if (e.volume, e.year, key) in PRINTED:
                p.accept(
                    "accounting.votes_valid",
                    "Riksdagsmannavalen år 1921, table 5, prints Kristianstads läns valkrets with party ballots that "
                    "add up to 62,304 in the rural districts against a printed total of 62,305, and to 7,714 in the "
                    "towns against 7,713; the national rows of the two parts repeat the difference, and the "
                    "constituency's total is right. The figures are stored as printed.",
                )
    _write(b, volume)
    return p


def _write(b: Build, volume: Volume) -> None:
    p = b.p
    artifact = raw.cached(SOURCE, volume.url)
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{volume.source_id}\x1f{artifact.storage_key}"))
    sizes = page_sizes()
    for page in sorted(b.pages):
        width, height = sizes[(volume.key, page)]
        p.add(
            "source_page",
            id=page_id(volume, page),
            artifact_id=artifact_id,
            page_number=page,
            image_key=f"pages/{SOURCE}/{artifact.sha256}-{page}.png",
            width=width,
            height=height,
        )
    p.frame(
        "result_value",
        pl.DataFrame(
            b.values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "flags": pl.Int16,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            b.boxes,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "page_id": pl.String,
                "x0": pl.Float64,
                "y0": pl.Float64,
                "x1": pl.Float64,
                "y1": pl.Float64,
            },
            orient="row",
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
        ),
    )


# ---------------------------------------------------------------------------------------------------------------------
# Locating the figures. `locate` is run by hand after a table is transcribed: it renders each table page at 300 dpi,
# reads its words and their boxes with Tesseract, and finds the transcribed figures among them. A figure of 100 or
# more that only one line of the page spells anchors its row to that line; the rows between anchors are placed by
# their order. A figure the OCR read differently takes its row's band and its column's median edges. The boxes go to
# locators.tsv and the page sizes to pages.tsv, and it reports how many figures the OCR read the same as the
# transcription. Seats are outcomes and need no box.

type Word = tuple[str, int, int, int, int]


def _words(pdf: Path, page: int, folder: Path) -> tuple[list[Word], int, int]:
    import os
    import subprocess

    from PIL import Image

    stem = folder / f"{page}"
    subprocess.run(
        ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", pdf, stem],
        check=True,
    )
    image = stem.with_suffix(".png")
    width, height = Image.open(image).size
    tsv = subprocess.run(
        [
            "tesseract",
            image,
            "-",
            "--psm",
            "6",
            "-l",
            "eng",
            # Table finding stalls for minutes on some of the ruled pages and adds nothing to a word list.
            "-c",
            "textord_tabfind_find_tables=0",
            "-c",
            "textord_disable_pitch_test=1",
            "tsv",
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    ).stdout
    words: list[Word] = []
    for r in csv.DictReader(tsv.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE):
        if r["level"] == "5" and r["text"].strip():
            left, top, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
            words.append((r["text"], left, top, left + w, top + h))
    return words, width, height


def _lines(words: list[Word]) -> list[list[Word]]:
    found: list[tuple[float, list[Word]]] = []
    for w in sorted(words, key=lambda w: (w[2] + w[4]) / 2):
        middle = (w[2] + w[4]) / 2
        if found and abs(middle - found[-1][0]) < 14:
            group = found[-1][1] + [w]
            found[-1] = (sum((v[2] + v[4]) / 2 for v in group) / len(group), group)
        else:
            found.append((middle, [w]))
    return [sorted(group, key=lambda w: w[1]) for _, group in found]


def _numbers(line: list[Word]) -> list[tuple[int, tuple[int, int, int, int]]]:
    """Every number a run of up to three adjacent tokens can spell, with the run's box."""
    import re

    tokens = [(re.sub(r"\D", "", w[0]), w) for w in line]
    out: list[tuple[int, tuple[int, int, int, int]]] = []
    for i, (digits, w) in enumerate(tokens):
        if not digits:
            continue
        text, box = digits, [w[1], w[2], w[3], w[4]]
        out.append((int(text), (box[0], box[1], box[2], box[3])))
        for nxt, v in tokens[i + 1 : i + 3]:
            if len(nxt) != 3 or v[1] - box[2] > 40:
                break
            text += nxt
            box = [box[0], min(box[1], v[2]), v[3], max(box[3], v[4])]
            out.append((int(text), (box[0], box[1], box[2], box[3])))
    return out


def _increasing(points: list[tuple[int, float]], tall: float) -> list[tuple[int, float]]:
    """The longest run of (row, y) points whose y rises with the row at a plausible pace: at least half a line and at
    most eight lines per row, with six lines to spare for the subtotals and headings between them."""
    best: list[list[tuple[int, float]]] = []
    for point in sorted(points):
        chains = [
            chain
            for chain in best
            if chain[-1][0] < point[0]
            and 0.5 * tall * (point[0] - chain[-1][0])
            <= point[1] - chain[-1][1]
            <= 8 * tall * (point[0] - chain[-1][0]) + 6 * tall
        ]
        best.append([*max(chains, key=len, default=[]), point])
    return max(best, key=len, default=[])


def _interpolate(anchors: list[tuple[int, float]], index: int) -> float:
    before = [a for a in anchors if a[0] <= index]
    after = [a for a in anchors if a[0] >= index]
    if before and after:
        (i0, y0), (i1, y1) = before[-1], after[0]
    elif len(anchors) >= 2:
        (i0, y0), (i1, y1) = (anchors[0], anchors[1]) if not before else (anchors[-2], anchors[-1])
    else:
        return anchors[0][1]
    return y0 if i0 == i1 else y0 + (index - i0) * (y1 - y0) / (i1 - i0)


@dataclass
class Scan:
    """One table page as the OCR read it, and the rows of the table it anchors."""

    page: int
    columns: list[str]
    lines: list[list[Word]]
    spelled: list[list[tuple[int, tuple[int, int, int, int]]]]
    middles: list[float]
    pitch: float
    exact: dict[tuple[int, str], tuple[int, int, int, int]]
    anchors: list[tuple[int, float]]
    profile: list[float]  # the share of dark pixels in each pixel row, for aligning facing pages


def _scan(
    pdf: Path, page: int, here: list[str], found: list[tuple[int, str, Row]], folder: Path
) -> tuple[Scan, int, int]:
    import statistics

    import numpy as np
    from PIL import Image

    words, width, height = _words(pdf, page, folder)
    dark = np.asarray(Image.open(folder / f"{page}.png").convert("L")) < 128
    profile = [float(v) for v in dark.mean(axis=1)]
    lines = _lines(words)
    spelled = [_numbers(line) for line in lines]
    middles = [statistics.median((w[2] + w[4]) / 2 for w in line) for line in lines]
    heights = [statistics.median(w[4] - w[2] for w in line) for line in lines]
    # Anchors: a figure of 100 or more that one line of the page spells, and only one row of the table holds, places
    # its row on that line; the longest run of anchors that descend the page with the rows is kept.
    counts: dict[int, int] = {}
    for _, _, row in found:
        for column in here:
            if (value := row.get(column)) is not None:
                counts[value] = counts.get(value, 0) + 1
    tall = statistics.median(heights) if heights else 30.0
    points: list[tuple[int, float]] = []
    exact: dict[tuple[int, str], tuple[int, int, int, int]] = {}
    for index, (_, _, row) in enumerate(found):
        for column in here:
            value = row.get(column)
            if value is None or value < 100 or counts[value] > 1:
                continue
            where = [
                (li, box)
                for li, numbers in enumerate(spelled)
                for n, box in numbers
                if n == value and box[3] - box[1] < 1.6 * tall
            ]
            if len({li for li, _ in where}) == 1:
                li, box = where[0]
                points.append((index, middles[li]))
                exact[(index, column)] = box
    anchors = _increasing(points, tall)
    rows = {a[0] for a in anchors}
    exact = {k: v for k, v in exact.items() if k[0] in rows}
    pitch = statistics.median(heights) / 2 + 2 if heights else 10.0
    return Scan(page, here, lines, spelled, middles, pitch, exact, anchors, profile), width, height


def _offset(mine: list[float], theirs: list[float], reach: int = 200) -> float:
    """How far down this page sits from the facing page: the shift that best correlates their rows of ink."""
    import numpy as np

    a, b = np.asarray(mine), np.asarray(theirs)
    n = min(len(a), len(b)) - 2 * reach
    if n <= 0:
        return 0.0
    middle = a[reach : reach + n] - a[reach : reach + n].mean()
    scores = []
    for shift in range(-reach, reach + 1):
        window = b[reach - shift : reach - shift + n]
        scores.append(float(np.dot(middle, window - window.mean())))
    return float(int(np.argmax(scores)) - reach)


def _near(read: int, value: int) -> bool:
    """Whether the OCR's reading differs from a figure in one digit, or two in a figure of five or more."""
    a, b = str(read), str(value)
    if len(a) != len(b):
        return False
    return sum(x != y for x, y in zip(a, b, strict=True)) <= (2 if len(b) >= 5 else 1)


def _line(scan: Scan, y: float) -> int:
    return min(range(len(scan.lines)), key=lambda li: abs(scan.middles[li] - y))


def _fit(scan: Scan, anchors: list[tuple[int, float]], index: int, row: Row) -> int:
    """How many of a row's figures the line where a page would print it spells."""
    if not anchors or not scan.lines:
        return -1
    li = _line(scan, _interpolate(anchors, index))
    spelled = {n for n, _ in scan.spelled[li]}
    return sum(
        1 for column in scan.columns if (value := row.get(column)) is not None and value >= 10 and value in spelled
    )


# The left page of table 9 that ends each constituency of 1964 with its row for the whole constituency, in the order
# of the TSV; the national row (hela riket) follows Norrbotten's on the last page.
PAGES_1964 = [42, 54, 58, 66, 74, 82, 88, 98, 100, 104, 114, 120, 130, 134, 138, 146, 154, 160, 170, 182, 188, 194]
PAGES_1964 += [204, 212, 222, 230, 244, 254, 254]
LEFT_1964 = ("registered", "invalid", "total")


def _locate_1964(
    volume: Volume, found: list[tuple[int, str, Row]], folder: Path, sizes: list[list[object]]
) -> dict[tuple[int, str], tuple[int, float, float, float, float]]:
    """Each row for a whole constituency, found by its label (hela valkretsen, hela riket) on its left page, and the
    line at the same height on the right page, aligned by their rows of ink."""
    import statistics

    pdf = raw.cached(SOURCE, volume.url).path
    boxes: dict[tuple[int, str], tuple[int, float, float, float, float]] = {}
    scans: dict[int, tuple[list[list[Word]], list[float]]] = {}

    def scan(page: int) -> tuple[list[list[Word]], list[float]]:
        if page not in scans:
            import numpy as np
            from PIL import Image

            words, width, height = _words(pdf, page, folder)
            sizes.append([volume.key, page, round(width / 3), round(height / 3)])
            dark = np.asarray(Image.open(folder / f"{page}.png").convert("L")) < 128
            scans[page] = (_lines(words), [float(v) for v in dark.mean(axis=1)])
        return scans[page]

    seen: dict[int, int] = {}
    for index, page in enumerate(PAGES_1964):
        lines, profile = scan(page)
        labelled = [
            line
            for line in lines
            if any(("VALKRETS" in w[0].upper() or "RIKET" in w[0].upper()) for w in line[:3])
            and any(w[0][:1].isdigit() for w in line)
        ]
        which = seen.get(page, 0)
        seen[page] = which + 1
        if which >= len(labelled):
            continue
        left = labelled[which]
        y = statistics.median((w[2] + w[4]) / 2 for w in left)
        right_lines, right_profile = scan(page + 1)
        shift = _offset(right_profile, profile)
        middles = [statistics.median((w[2] + w[4]) / 2 for w in line) for line in right_lines]
        right = right_lines[min(range(len(right_lines)), key=lambda li: abs(middles[li] - (y + shift)))]
        row = found[index][2]
        for line, columns, at in ((left, LEFT_1964, page), (right, party_columns(volume.key), page + 1)):
            numbers = _numbers(line)
            for column in columns:
                value = row.get(column)
                if value is None or value < 10:
                    continue
                match = [box for n, box in numbers if n == value] or [box for n, box in numbers if _near(n, value)]
                if len(match) >= 1:
                    box = match[0]
                    boxes[(index, column)] = (at, box[0] / 3, box[1] / 3, box[2] / 3, box[3] / 3)
    return boxes


def locate() -> None:
    import itertools
    import statistics
    import tempfile

    out: list[list[object]] = []
    sizes: list[list[object]] = []
    for volume in VOLUMES.values():
        pdf = raw.cached(SOURCE, volume.url).path
        found = rows(volume.key)
        cols = columns(volume.key)
        boxes: dict[tuple[int, str], tuple[int, float, float, float, float]] = {}
        if not volume.layout:
            with tempfile.TemporaryDirectory() as folder:
                boxes = _locate_1964(volume, found, Path(folder), sizes)
            for index, (year, krets, row) in enumerate(found):
                out += [[volume.key, year, krets, c, *boxes[(index, c)]] for c in cols if (index, c) in boxes]
            print(f"{volume.key}: {len(boxes)} figures boxed")
            continue
        read: set[tuple[int, str]] = set()
        near: set[tuple[int, str]] = set()
        scans: list[Scan] = []
        with tempfile.TemporaryDirectory() as folder:
            for page in volume.pages:
                scan, width, height = _scan(pdf, page, volume.page_columns(page), found, Path(folder))
                scans.append(scan)
                sizes.append([volume.key, page, round(width / 3), round(height / 3)])
        # A spread is a left page and the right page facing it, which print the same rows at the same heights; their
        # anchors are pooled. Each row belongs to the spread that anchors it most; a row no spread anchors goes with
        # the rows around it, to the spread whose line at its height spells more of it.
        spreads: list[list[Scan]] = []
        for scan in scans:
            last = spreads[-1] if spreads else None
            if (
                last is not None
                and len(last) == 1
                and scan.page == last[0].page + 1
                and scan.columns != last[0].columns
                and any(not c.startswith("seats") for c in scan.columns)
            ):
                last.append(scan)
            else:
                spreads.append([scan])
        pooled: dict[int, list[tuple[int, float]]] = {}
        for spread in spreads:
            for scan in spread:
                # The facing page's anchors, shifted by the offset that best lines up the two pages' rows of ink;
                # pages scanned apart sit at different heights.
                points = dict(scan.anchors)
                for other in spread:
                    if other is scan:
                        continue
                    offset = _offset(scan.profile, other.profile)
                    for index, y in other.anchors:
                        points.setdefault(index, y + offset)
                pooled[scan.page] = _increasing(sorted(points.items()), 2 * scan.pitch - 4)
        layouts: dict[str, list[list[Scan]]] = {}
        for spread in spreads:
            layouts.setdefault(" | ".join(" ".join(s.columns) for s in spread), []).append(spread)
        owner: dict[tuple[str, int], int] = {}  # (layout, row) -> position of the spread in the layout
        for layout, members in layouts.items():
            tally: dict[int, dict[int, int]] = {}
            for position, spread in enumerate(members):
                for index, _ in pooled[spread[0].page]:
                    tally.setdefault(index, {})[position] = tally.get(index, {}).get(position, 0) + 1
            mine = {index: max(t, key=lambda k: t[k]) for index, t in tally.items()}
            known = sorted(mine)
            for index in range(len(found)):
                if index in mine or not known:
                    continue
                before = max((k for k in known if k < index), default=None)
                after = min((k for k in known if k > index), default=None)
                candidates = sorted({mine[k] for k in (before, after) if k is not None})
                mine[index] = max(
                    candidates,
                    key=lambda position: sum(
                        _fit(scan, pooled[scan.page], index, found[index][2]) for scan in members[position]
                    ),
                )
            for index, position in mine.items():
                owner[(layout, index)] = position
        sibling: dict[str, list[tuple[int, int, int, int]]] = {}  # each column's hits on every page, by layout
        work: list[tuple[Scan, list[int], list[tuple[int, float]]]] = []
        for layout, members in layouts.items():
            for position, spread in enumerate(members):
                mine = sorted(index for (lay, index), p in owner.items() if lay == layout and p == position)
                for scan in spread:
                    anchors = [a for a in pooled[scan.page] if a[0] in mine]
                    if mine and anchors:
                        work.append((scan, mine, anchors))
        hits: dict[int, dict[str, list[tuple[int, int, int, int]]]] = {}
        bands: dict[int, dict[int, float]] = {}
        for scan, mine, anchors in work:
            found_here = hits.setdefault(scan.page, {})
            for (index, column), box in scan.exact.items():
                if index in mine:
                    found_here.setdefault(column, []).append(box)
                    boxes[(index, column)] = (scan.page, box[0] / 3, box[1] / 3, box[2] / 3, box[3] / 3)
                    read.add((index, column))
            # A row beyond the page's first or last anchor is placed on it only within one and a half rows of it.
            ordered = sorted(anchors)
            steps = [(b[1] - a[1]) / (b[0] - a[0]) for a, b in itertools.pairwise(ordered) if b[0] > a[0]]
            step = statistics.median(steps) if steps else 4 * scan.pitch
            top, bottom = ordered[0][1] - 1.5 * step, ordered[-1][1] + 1.5 * step
            band = bands[scan.page] = {
                index: y for index in mine if top <= (y := _interpolate(anchors, index)) <= bottom
            }
            # Smaller figures, read on the line their row's band falls on.
            for index, y in band.items():
                li = _line(scan, y)
                if abs(scan.middles[li] - y) > scan.pitch:
                    continue
                for column in scan.columns:
                    value = found[index][2].get(column)
                    if value is None or value < 10 or (index, column) in boxes or column.startswith("seats"):
                        continue
                    spans = [box for n, box in scan.spelled[li] if n == value]
                    if len(spans) == 1:
                        box = spans[0]
                        found_here.setdefault(column, []).append(box)
                        boxes[(index, column)] = (scan.page, box[0] / 3, box[1] / 3, box[2] / 3, box[3] / 3)
                        read.add((index, column))
            for column, bs in found_here.items():
                sibling.setdefault(" ".join(scan.columns) + "\x1f" + column, []).extend(bs)
        # The rest take their row's band and their column's median edges: on this page, else on the pages of the
        # same layout, else between the columns beside it.
        for scan, _, _ in work:
            edges = {
                column: (statistics.median(b[0] for b in bs), statistics.median(b[2] for b in bs))
                for column, bs in hits[scan.page].items()
                if len(bs) >= 2
            }
            order = [c for c in scan.columns if not c.startswith("seats")]
            for column in order:
                others = sibling.get(" ".join(scan.columns) + "\x1f" + column, [])
                if column not in edges and len(others) >= 2:
                    edges[column] = (statistics.median(b[0] for b in others), statistics.median(b[2] for b in others))
            for position, column in enumerate(order):
                if column in edges:
                    continue
                left = next((edges[c][1] for c in reversed(order[:position]) if c in edges), None)
                right = next((edges[c][0] for c in order[position + 1 :] if c in edges), None)
                if left is not None and right is not None and right > left:
                    gap = (right - left) / 4
                    edges[column] = (left + gap, right - gap)
            band = bands[scan.page]
            # A figure the OCR misread by a digit or two, in its column and near its row's band.
            for index, y in band.items():
                for column, (x0, x1) in edges.items():
                    value = found[index][2].get(column)
                    if value is None or value < 10 or (index, column) in boxes:
                        continue
                    best: tuple[float, tuple[int, int, int, int]] | None = None
                    for li, numbers in enumerate(scan.spelled):
                        distance = abs(scan.middles[li] - y)
                        if distance > 3 * scan.pitch:
                            continue
                        for n, box in numbers:
                            inside = x0 - 20 <= (box[0] + box[2]) / 2 <= x1 + 20
                            if inside and _near(n, value) and (best is None or distance < best[0]):
                                best = (distance, box)
                    if best is not None:
                        box = best[1]
                        boxes[(index, column)] = (scan.page, box[0] / 3, box[1] / 3, box[2] / 3, box[3] / 3)
                        near.add((index, column))
            # Each row's band again, from the figures now placed on it; then the rest of its figures.
            placed_rows: dict[int, list[float]] = {}
            for (index, column), box in boxes.items():
                if box[0] == scan.page and index in band and ((index, column) in read or (index, column) in near):
                    placed_rows.setdefault(index, []).append((box[2] + box[4]) / 2 * 3)
            # A row's other figures take its band, where the OCR confirmed at least one figure of the row on this
            # page, and their column's edges; a row with none is not boxed, rather than boxed by a guess.
            for index in band:
                if index not in placed_rows:
                    continue
                y = statistics.median(placed_rows[index])
                for column, (x0, x1) in edges.items():
                    if found[index][2].get(column) is not None and (index, column) not in boxes:
                        top, bottom = (y - scan.pitch) / 3, (y + scan.pitch) / 3
                        boxes[(index, column)] = (scan.page, x0 / 3, top, x1 / 3, bottom)
        placed = wanted = 0
        for index, (year, krets, row) in enumerate(found):
            for column in cols:
                if row[column] is None or column.startswith("seats"):
                    continue
                wanted += 1
                if (index, column) in boxes:
                    placed += 1
                    out.append([volume.key, year, krets, column, *boxes[(index, column)]])
        print(
            f"{volume.key}: {wanted} figures, {placed} boxed, {len(read)} read by the OCR as transcribed, "
            f"{len(near)} within a digit or two"
        )
    with (FOLDER / "locators.tsv").open("w", encoding="utf-8") as f:
        f.write("volume\tyear\tkrets\tcolumn\tpage\tx0\ty0\tx1\ty1\n")
        for r in out:
            f.write("\t".join(str(round(v, 1)) if isinstance(v, float) else str(v) for v in r) + "\n")
    with (FOLDER / "pages.tsv").open("w", encoding="utf-8") as f:
        f.write("volume\tpage\twidth\theight\n")
        for r in sizes:
            f.write("\t".join(str(v) for v in r) + "\n")
