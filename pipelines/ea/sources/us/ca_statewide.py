import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.ca_statewide"
RAW = "us.ca_sov"
VERSION = "1"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote, General Election, {day:%B %-d, %Y}"
LICENSE = "Public record"
STATE = "CA"
FIPS = "06"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
TITLES = {
    "governor": "Governor",
    "lieutenant-governor": "Lieutenant Governor",
    "secretary-of-state": "Secretary of State",
    "controller": "Controller",
    "treasurer": "Treasurer",
    "attorney-general": "Attorney General",
    "surveyor-general": "Surveyor General",
    "clerk-of-the-supreme-court": "Clerk of the Supreme Court",
    "superintendent-of-public-instruction": "Superintendent of Public Instruction",
    "superintendent-of-state-printing": "Superintendent of State Printing",
    "supreme-court-justice": "Associate Justice of the Supreme Court",
}
NOT_VOTES = {"Total", "Blank", "Void", "Scattering"}
ITEMS = {
    1882: (date(1882, 11, 7), (3, 4, 6, 7, 8, 9, 10, 11, 12)),
    1886: (date(1886, 11, 2), range(11, 20)),
    1910: (date(1910, 11, 8), (3, *range(5, 13))),
    1914: (date(1914, 11, 3), (9, 10, 11, 12, 13, 14, 19)),
    1918: (date(1918, 11, 5), (8, 9, 10, 11, 12, 13, 16)),
    1922: (date(1922, 11, 7), range(13, 20)),
    1926: (date(1926, 11, 2), range(5, 12)),
    1930: (date(1930, 11, 4), range(5, 10)),
    1934: (date(1934, 11, 6), range(6, 11)),
    1938: (date(1938, 11, 8), range(5, 10)),
    1942: (date(1942, 11, 3), range(5, 10)),
    1946: (date(1946, 11, 5), range(5, 10)),
    1950: (date(1950, 11, 7), range(5, 10)),
    1954: (date(1954, 11, 2), range(4, 9)),
    1958: (date(1958, 11, 4), range(4, 9)),
    1974: (date(1974, 11, 5), range(10, 15)),
    1978: (date(1978, 11, 7), range(12, 17)),
    1982: (date(1982, 11, 2), range(20, 25)),
    1986: (date(1986, 11, 4), range(19, 24)),
}
VOLUMES = {
    year: (f"https://v3.californiatargetbook.com/docs/SoV/g{year}.pdf", "https://www.californiatargetbook.com/", day, pages)
    for year, (day, pages) in ITEMS.items()
}
PARTIES = {
    (1882, "governor", "M. M. Estee"): "republican-party",
    (1882, "governor", "Geo. Stoneman"): "democratic-party",
    (1882, "lieutenant-governor", "John Daggett"): "republican-party",
    (1882, "lieutenant-governor", "A. R. Conklin"): "democratic-party",
    (1882, "secretary-of-state", "Thos. L. Thompson"): "democratic-party",
    (1882, "controller", "John P. Dunn"): "democratic-party",
    (1882, "attorney-general", "A. L. Hart"): "democratic-party",
    (1886, "governor", "John F. Swift"): "republican-party",
    (1886, "governor", "Washington Bartlett"): "democratic-party",
    (1886, "governor", "Joel Russell"): "prohibition-party",
    (1886, "lieutenant-governor", "R. H. Waterman"): "republican-party",
    (1886, "lieutenant-governor", "Michael F. Tarpey"): "democratic-party",
    (1886, "lieutenant-governor", "A. D. Boren"): "prohibition-party",
    (1886, "secretary-of-state", "Walter S. Moore"): "republican-party",
    (1886, "secretary-of-state", "William C. Hendricks"): "democratic-party",
    (1886, "secretary-of-state", "Frank E. Kellogg"): "prohibition-party",
    (1886, "controller", "J. E. Denny"): "republican-party",
    (1886, "controller", "John P. Dunn"): "democratic-party",
    (1886, "controller", "J. A. Fairbanks"): "prohibition-party",
    (1886, "treasurer", "J. H. Neff"): "republican-party",
    (1886, "treasurer", "Adam Herold"): "democratic-party",
    (1886, "treasurer", "H. S. Graves"): "prohibition-party",
    (1886, "attorney-general", "W. H. H. Hart"): "republican-party",
    (1886, "attorney-general", "George A. Johnson"): "democratic-party",
    (1886, "attorney-general", "George Babcock"): "prohibition-party",
    (1886, "surveyor-general", "Theodore Reichert"): "republican-party",
    (1886, "surveyor-general", "George B. Tolman"): "prohibition-party",
    (1886, "clerk-of-the-supreme-court", "Julius Lyons"): "prohibition-party",
    (1886, "superintendent-of-public-instruction", "D. A. Mobley"): "prohibition-party",
    (1910, "lieutenant-governor", "Albert J. Wallace"): "republican-party",
    (1910, "lieutenant-governor", "Timothy Spellacy"): "democratic-party",
    (1910, "lieutenant-governor", "Fred C. Wheeler"): "socialist-party-of-america",
    (1910, "lieutenant-governor", "M. W. Atwood"): "prohibition-party",
    (1910, "secretary-of-state", "Frank C. Jordan"): "republican-party",
    (1910, "secretary-of-state", "Simeon S. Bayley"): "democratic-party",
    (1910, "secretary-of-state", "Edward A. Cantrell"): "socialist-party-of-america",
    (1910, "secretary-of-state", "Lucius C. Dale"): "prohibition-party",
    (1910, "controller", "W. S. Deeds"): "socialist-party-of-america",
    (1910, "controller", "C. V. Sturdevant"): "prohibition-party",
    (1910, "treasurer", "W. R. Williams"): "republican-party",
    (1910, "treasurer", "Tupper S. Malone"): "democratic-party",
    (1910, "treasurer", "A. F. Briggs"): "socialist-party-of-america",
    (1910, "treasurer", "W. P. Fassett"): "prohibition-party",
    (1910, "attorney-general", "U. S. Webb"): "republican-party",
    (1910, "attorney-general", "J. E. Pemberton"): "democratic-party",
    (1910, "attorney-general", "Henry L. Ford"): "socialist-party-of-america",
    (1910, "attorney-general", "Wallace M. Pence"): "prohibition-party",
    (1910, "surveyor-general", "Wm. S. Kingsbury"): "republican-party",
    (1910, "surveyor-general", "E. W. Nolan"): "democratic-party",
    (1910, "surveyor-general", "R. Gillonsen"): "socialist-party-of-america",
    (1910, "surveyor-general", "Harry V. Wheeler"): "prohibition-party",
    (1910, "clerk-of-the-supreme-court", "B. Grant Taylor"): "republican-party",
    (1910, "clerk-of-the-supreme-court", "Hiram A. Blanchard"): "democratic-party",
    (1910, "clerk-of-the-supreme-court", "M. E. Shore"): "socialist-party-of-america",
    (1910, "clerk-of-the-supreme-court", "Frederick Head"): "prohibition-party",
    (1910, "superintendent-of-public-instruction", "Edward Hyatt"): "republican-party",
    (1910, "superintendent-of-public-instruction", "Thomas H. Kirk"): "democratic-party",
    (1910, "superintendent-of-public-instruction", "Agnes H. Downing"): "socialist-party-of-america",
    (1910, "superintendent-of-public-instruction", "Perry O. Cole"): "prohibition-party",
    (1910, "superintendent-of-state-printing", "Wm. W. Shannon"): "republican-party",
    (1910, "superintendent-of-state-printing", "D. W. Ravenscroft"): "democratic-party",
    (1910, "superintendent-of-state-printing", "Francis M. Elliott"): "socialist-party-of-america",
    (1910, "superintendent-of-state-printing", "Clarence J. Kinne"): "prohibition-party",
    (1914, "lieutenant-governor", "Jo V. Snyder"): "democratic-party",
    (1914, "lieutenant-governor", "Norman W. Pendleton"): "socialist-party-of-america",
    (1914, "lieutenant-governor", "Wallace M. Pence"): "prohibition-party",
    (1914, "secretary-of-state", "F. J. O'Brien"): "progressive-party-1912",
    (1914, "secretary-of-state", "Ethel Lynn"): "socialist-party-of-america",
    (1914, "secretary-of-state", "Gabrella T. Stickney"): "prohibition-party",
    (1914, "controller", "A. E. Briggs"): "socialist-party-of-america",
    (1914, "controller", "Horace A. Johnson"): "prohibition-party",
    (1914, "treasurer", "George W. Woodbey"): "socialist-party-of-america",
    (1914, "treasurer", "Albert S. Spaulding"): "prohibition-party",
    (1914, "attorney-general", "Walter R. Dunn"): "socialist-party-of-america",
    (1914, "attorney-general", "James H. Blanchard"): "prohibition-party",
    (1914, "surveyor-general", "Frank E. Wright"): "republican-party",
    (1914, "surveyor-general", "John H. Kendall"): "prohibition-party",
    (1918, "lieutenant-governor", "Jo V. Snyder"): "democratic-party",
    (1918, "lieutenant-governor", "Elvina S. Beals"): "socialist-party-of-america",
    (1918, "secretary-of-state", "James S. Edwards"): "prohibition-party",
    (1918, "controller", "John C. Taylor"): "socialist-party-of-america",
    (1918, "controller", "Horace A. Johnson"): "prohibition-party",
    (1918, "treasurer", "H. K. Albright"): "socialist-party-of-america",
    (1918, "treasurer", "T. K. Beard"): "prohibition-party",
    (1918, "attorney-general", "Harry M. McKee"): "socialist-party-of-america",
    (1918, "attorney-general", "Thomas M. Stewart"): "prohibition-party",
    (1918, "surveyor-general", "Harry V. Wheeler"): "prohibition-party",
    (1922, "lieutenant-governor", "Isabel C. King"): "socialist-party-of-america",
    (1922, "lieutenant-governor", "William B. Shearer"): "democratic-party",
    (1922, "lieutenant-governor", "C. C. Young"): "republican-party",
    (1922, "secretary-of-state", "James P. M. Jensen"): "prohibition-party",
    (1922, "controller", "Miles Hammond"): "democratic-party",
    (1922, "controller", "Ray L. Riley"): "republican-party",
    (1922, "treasurer", "Charles G. Johnson"): "republican-party",
    (1922, "treasurer", "Edward A. Martin"): "democratic-party",
    (1926, "lieutenant-governor", "Buron Fitts"): "republican-party",
    (1926, "lieutenant-governor", "Charles O. Dunbar"): "democratic-party",
    (1926, "lieutenant-governor", "Lena Morrow Lewis"): "socialist-party-of-america",
    (1926, "secretary-of-state", "Frank C. Jordan"): "republican-party",
    (1926, "secretary-of-state", "Milton Bryan"): "democratic-party",
    (1926, "secretary-of-state", "James P. M. Jensen"): "prohibition-party",
    (1926, "controller", "Ray L. Riley"): "republican-party",
    (1926, "controller", "Louis H. Mooser"): "democratic-party",
    (1926, "controller", "Julius Levitt"): "socialist-party-of-america",
    (1926, "treasurer", "Charles G. Johnson"): "republican-party",
    (1926, "treasurer", "Noah L. Levering"): "democratic-party",
    (1926, "surveyor-general", "W. S. Kingsbury"): "republican-party",
    (1930, "lieutenant-governor", "Frank F. Merriam"): "republican-party",
    (1930, "lieutenant-governor", "Martin I. Welsh"): "democratic-party",
    (1930, "lieutenant-governor", "Chaim Shapiro"): "socialist-party-of-america",
    (1930, "secretary-of-state", "Frank C. Jordan"): "republican-party",
    (1930, "secretary-of-state", "William Maddux"): "democratic-party",
    (1930, "secretary-of-state", "M. Len Hutchins"): "prohibition-party",
    (1930, "controller", "Ray L. Riley"): "republican-party",
    (1930, "controller", "Henry E. Harwood"): "democratic-party",
    (1930, "treasurer", "Charles G. Johnson"): "republican-party",
    (1930, "treasurer", "William B. Shearer"): "democratic-party",
    (1930, "treasurer", "Sylvanus H. Kellogg"): "prohibition-party",
    (1934, "lieutenant-governor", "Sheridan Downey"): "democratic-party",
    (1934, "lieutenant-governor", "George J. Hatfield"): "republican-party",
    (1934, "lieutenant-governor", "Jesse W. Southwick"): "socialist-party-of-america",
    (1934, "treasurer", "Charles G. Johnson"): "republican-party",
    (1934, "treasurer", "Wm. A. Ward"): "democratic-party",
    (1938, "lieutenant-governor", "Walter Scott Franklin"): "republican-party",
    (1938, "secretary-of-state", "Nellie S. Harriss"): "prohibition-party",
    (1938, "controller", "Ellen G. Pritchard"): "prohibition-party",
    (1938, "attorney-general", "Claude A. Watson"): "prohibition-party",
    (1942, "lieutenant-governor", "Ellis E. Patterson"): "democratic-party",
    (1942, "lieutenant-governor", "Frederick F. Houser"): "republican-party",
    (1942, "secretary-of-state", "Paul Peek"): "democratic-party",
    (1942, "secretary-of-state", "Frank M. Jordan"): "republican-party",
    (1942, "attorney-general", "Robert W. Kenny"): "democratic-party",
    (1942, "attorney-general", "Wallace L. Ware"): "republican-party",
    (1942, "attorney-general", "Claude A. Watson"): "prohibition-party",
    (1946, "lieutenant-governor", "Goodwin J. Knight"): "republican-party",
    (1946, "lieutenant-governor", "John F. Shelley"): "democratic-party",
    (1946, "controller", "Virgil G. Hinshaw"): "prohibition-party",
    (1946, "attorney-general", "Edmund G. Brown"): "democratic-party",
    (1946, "attorney-general", "Fred N. Houser"): "republican-party",
    (1946, "attorney-general", "Claude A. Watson"): "prohibition-party",
    (1950, "controller", "Virgil G. Hinshaw"): "prohibition-party",
    (1950, "attorney-general", "Edmund G. (Pat) Brown"): "democratic-party",
    (1950, "attorney-general", "Claude A. Watson"): "prohibition-party",
    (1950, "attorney-general", "Edward S. Shattuck"): "republican-party",
    (1954, "lieutenant-governor", "Harold J. Powers"): "republican-party",
    (1954, "lieutenant-governor", "Edward R. Roybal"): "democratic-party",
    (1954, "secretary-of-state", "Frank M. Jordan"): "republican-party",
    (1954, "secretary-of-state", "Daniel Raeburn"): "democratic-party",
    (1954, "secretary-of-state", "Frank G. H. Stevens"): "prohibition-party",
    (1954, "controller", "Robert C. Kirkwood"): "republican-party",
    (1954, "controller", "George D. Collins Jr."): "democratic-party",
    (1954, "controller", "Fred H. Ross"): "prohibition-party",
    (1954, "treasurer", "Charles G. Johnson"): "republican-party",
    (1954, "treasurer", "George E. Johnson"): "democratic-party",
    (1954, "attorney-general", "Edwin M. Copper"): "prohibition-party",
    (1958, "lieutenant-governor", "Harold J. Powers"): "republican-party",
    (1958, "lieutenant-governor", "Glenn M. Anderson"): "democratic-party",
    (1958, "secretary-of-state", "Frank M. Jordan"): "republican-party",
    (1958, "secretary-of-state", "Henry P. Lopez"): "democratic-party",
    (1958, "secretary-of-state", "Mel J. Miller"): "prohibition-party",
    (1958, "controller", "Robert C. Kirkwood"): "republican-party",
    (1958, "controller", "Alan Cranston"): "democratic-party",
    (1958, "treasurer", "A. Ronald Button"): "republican-party",
    (1958, "treasurer", "Bert A. Betts"): "democratic-party",
    (1958, "attorney-general", "Patrick J. Hillings"): "republican-party",
    (1958, "attorney-general", "Stanley Mosk"): "democratic-party",
    (1974, "lieutenant-governor", "Mervyn M. Dymally"): "democratic-party",
    (1974, "lieutenant-governor", "John L. Harmer"): "republican-party",
    (1974, "lieutenant-governor", "Alberta M. Procell"): "american-independent-party",
    (1974, "lieutenant-governor", "Marilyn Seals"): "CA:peace-and-freedom-party",
    (1974, "secretary-of-state", "March K. Fong"): "democratic-party",
    (1974, "secretary-of-state", "Brian R. Van Camp"): "republican-party",
    (1974, "secretary-of-state", "Charles C. Ripley"): "american-independent-party",
    (1974, "secretary-of-state", "Kay McGlachlin"): "CA:peace-and-freedom-party",
    (1974, "controller", "Kenneth Cory"): "democratic-party",
    (1974, "controller", "William T. Bagley"): "republican-party",
    (1974, "controller", "Kevin Scanlon"): "american-independent-party",
    (1974, "controller", "Corey D. Cassanova"): "CA:peace-and-freedom-party",
    (1974, "treasurer", "Jesse M. Unruh"): "democratic-party",
    (1974, "treasurer", "John T. Kehoe"): "republican-party",
    (1974, "treasurer", "W. Wayne Fortner"): "american-independent-party",
    (1974, "attorney-general", "Evelle J. Younger"): "republican-party",
    (1974, "attorney-general", "William A. Norris"): "democratic-party",
    (1978, "lieutenant-governor", "Mervyn M. Dymally"): "democratic-party",
    (1978, "lieutenant-governor", "Ouston A. Myers"): "american-independent-party",
    (1978, "lieutenant-governor", "Jan B. Tucker"): "CA:peace-and-freedom-party",
    (1978, "lieutenant-governor", "Mike Curb"): "republican-party",
    (1978, "secretary-of-state", "March Fong Eu"): "democratic-party",
    (1978, "secretary-of-state", "David Wald"): "CA:peace-and-freedom-party",
    (1978, "secretary-of-state", "Jacob 'Jay' Margosian"): "republican-party",
    (1978, "secretary-of-state", "Valerie C. Seeman"): "american-independent-party",
    (1978, "controller", "Edmon V. Kaiser"): "american-independent-party",
    (1978, "controller", "Elizabeth Cervantes Barron"): "CA:peace-and-freedom-party",
    (1978, "controller", "James A. Ware"): "republican-party",
    (1978, "controller", "Kenneth Cory"): "democratic-party",
    (1978, "treasurer", "Thomas M. Goodloe Jr."): "american-independent-party",
    (1978, "treasurer", "Donald J. French"): "republican-party",
    (1978, "treasurer", "Jesse M. Unruh"): "democratic-party",
    (1978, "treasurer", "Lewis J. Shireman"): "CA:peace-and-freedom-party",
    (1978, "attorney-general", "Robert J. Evans"): "CA:peace-and-freedom-party",
    (1978, "attorney-general", "Yvonne Brathwaite Burke"): "democratic-party",
    (1978, "attorney-general", "George 'Duke' Deukmejian"): "republican-party",
    (1978, "attorney-general", "Dallas Wendell Reid"): "american-independent-party",
    (1982, "lieutenant-governor", "Carol Hallett"): "republican-party",
    (1982, "lieutenant-governor", "Clyde Kuhn"): "CA:peace-and-freedom-party",
    (1982, "lieutenant-governor", "Leo T. McCarthy"): "democratic-party",
    (1982, "lieutenant-governor", "Houston A. Myers"): "american-independent-party",
    (1982, "lieutenant-governor", "John R. Vernon"): "libertarian-party",
    (1982, "secretary-of-state", "Martin E. Buerger"): "libertarian-party",
    (1982, "secretary-of-state", "Gordon Duffy"): "republican-party",
    (1982, "secretary-of-state", "March Fong Eu"): "democratic-party",
    (1982, "secretary-of-state", "Alfred W. Smith"): "american-independent-party",
    (1982, "secretary-of-state", "Milton Shiro Takei"): "CA:peace-and-freedom-party",
    (1982, "controller", "Kenneth Cory"): "democratic-party",
    (1982, "controller", "James L. Flournoy"): "republican-party",
    (1982, "controller", "Mary Gingell"): "libertarian-party",
    (1982, "controller", "'Pat' Graham"): "american-independent-party",
    (1982, "controller", "Florence McDonald"): "CA:peace-and-freedom-party",
    (1982, "treasurer", "Kevin Akin"): "CA:peace-and-freedom-party",
    (1982, "treasurer", "Less Antman"): "libertarian-party",
    (1982, "treasurer", "Robert G. Charlton"): "american-independent-party",
    (1982, "treasurer", "Donald J. French"): "republican-party",
    (1982, "treasurer", "Jesse M. Unruh"): "democratic-party",
    (1982, "attorney-general", "Bartholomew (Bart) Lee"): "libertarian-party",
    (1982, "attorney-general", "George Nicholson"): "republican-party",
    (1982, "attorney-general", "Dan Siegel"): "CA:peace-and-freedom-party",
    (1982, "attorney-general", "John Van De Kamp"): "democratic-party",
    (1986, "lieutenant-governor", "Leo T. McCarthy"): "democratic-party",
    (1986, "lieutenant-governor", "Mike Curb"): "republican-party",
    (1986, "lieutenant-governor", "James C. 'Jim' Griffin"): "american-independent-party",
    (1986, "lieutenant-governor", "Norma Jean Almodovar"): "libertarian-party",
    (1986, "lieutenant-governor", "Clyde Kuhn"): "CA:peace-and-freedom-party",
    (1986, "secretary-of-state", "March Fong Eu"): "democratic-party",
    (1986, "secretary-of-state", "Bruce Nestande"): "republican-party",
    (1986, "secretary-of-state", "Gloria Garcia"): "CA:peace-and-freedom-party",
    (1986, "secretary-of-state", "Richard Winger"): "libertarian-party",
    (1986, "secretary-of-state", "Theresa 'Tena' Dietrich"): "american-independent-party",
    (1986, "controller", "Gray Davis"): "democratic-party",
    (1986, "controller", "Bill Campbell"): "republican-party",
    (1986, "controller", "Carolyn Treynor"): "libertarian-party",
    (1986, "controller", "John Haag"): "CA:peace-and-freedom-party",
    (1986, "controller", "Nicholas W. Kudrovzeff"): "american-independent-party",
    (1986, "treasurer", "Jesse M. Unruh"): "democratic-party",
    (1986, "treasurer", "Ray Cullen"): "libertarian-party",
    (1986, "treasurer", "Merton D. Short"): "american-independent-party",
    (1986, "treasurer", "Maureen Smith"): "CA:peace-and-freedom-party",
    (1986, "attorney-general", "John Van de Kamp"): "democratic-party",
    (1986, "attorney-general", "Bruce Gleason"): "republican-party",
    (1986, "attorney-general", "Carol L. Newman"): "libertarian-party",
    (1986, "attorney-general", "Robert J. Evans"): "CA:peace-and-freedom-party",
    (1986, "attorney-general", "Gary R. Odom"): "american-independent-party",
}
NAMES: dict[str, str] = {}
ERRATA = {
    (1882, ("secretary-of-state", 0)),
    (1882, ("treasurer", 0)),
    (1882, ("surveyor-general", 0)),
    (1882, ("clerk-of-the-supreme-court", 0)),
    (1882, ("superintendent-of-public-instruction", 0)),
    (1886, ("governor", 0)),
    (1886, ("lieutenant-governor", 0)),
    (1886, ("secretary-of-state", 0)),
    (1886, ("controller", 0)),
    (1886, ("attorney-general", 0)),
    (1886, ("surveyor-general", 0)),
    (1886, ("clerk-of-the-supreme-court", 0)),
    (1886, ("superintendent-of-public-instruction", 0)),
    (1910, ("attorney-general", 0)),
    (1910, ("surveyor-general", 0)),
    (1914, ("attorney-general", 0)),
    (1914, ("surveyor-general", 0)),
    (1914, ("superintendent-of-public-instruction", 0)),
    (1918, ("controller", 0)),
    (1918, ("treasurer", 0)),
    (1926, ("surveyor-general", 0)),
    (1930, ("controller", 0)),
    (1938, ("secretary-of-state", 0)),
    (1942, ("lieutenant-governor", 0)),
    (1946, ("secretary-of-state", 0)),
    (1946, ("controller", 0)),
    (1946, ("treasurer", 0)),
    (1958, ("treasurer", 0)),
}
PUBLISHED = {1882, 1886, 1910, 1914, 1918, 1922, 1926, 1930, 1934, 1938, 1942, 1946, 1950, 1954, 1958, 1974, 1978, 1982, 1986}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
COMMAS = True
TURNED: set[tuple[int, int]] = set()
TRANSPOSED = set(ITEMS)
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "values.parquet").filter(
        ~pl.col("year").map_elements(
            lambda year: year in PUBLISHED, return_dtype=pl.Boolean
        )
    )
    plain = house_books._plain
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's statewide offices by county from the Secretary of State's Statements of Vote",
            polity="US",
        )
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        PAGES.mkdir(parents=True, exist_ok=True)
        for year, (url, landing, day, numbers) in VOLUMES.items():
            if year not in set(frame["year"]):
                continue
            title = TITLE.format(day=day)
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source", id=sources[year], kind="official_body", publisher=PUBLISHER, title=title, url=landing,
                license=LICENSE,
            )
            book = house_books._artifact(sys.modules[__name__], year)
            artifact = cs.artifact(book, sources[year])
            for number in numbers:
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    house_books._render(book, number, 100, image, False)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                page = cs.page(artifact, number)
                pages[(year, number)] = (page, width, height)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        counties: dict[str, UUID] = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
            ).fetchall()
        }
        fptp = cs.key("us.electoral_system", "fptp")
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':CA:'")
        )
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election' and value ~ ':CA$'")
        )
        offices_known = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = 'us.office' and value ~ ':CA$'")
        }
        columns = (
            "id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness",
            "supersedes_id",
        )
        for (year, office), rows in frame.group_by("year", "state", maintain_order=True):
            year = int(year)
            day = VOLUMES[year][2]
            key = f"{day}:CA:{office}"
            result_set = cs.key(f"{SOURCE}.result_set", key)
            contest = contests.get(key)
            office_key = f"{office}:CA"
            if f"{day}:CA" not in elections:
                raise ValueError(f"{key}: no election")
            election = elections[f"{day}:CA"]
            if contest is None:
                contest = cs.key("us.contest", key)
                if office_key not in offices_known:
                    cs.add("office", id=cs.key("us.office", office_key), polity_id=cs.polity("US-CA"), kind="other")
                    cs.add(
                        "name", entity_id=cs.key("us.office", office_key), text=TITLES[office], lang="en",
                        kind="official", is_preferred=True,
                    )
                    offices_known.add(office_key)
                ((area,),) = conn.execute(
                    """select district_area_id from contest where id = %s""", [contests[f"{day}:CA:house:1"]]
                ).fetchall() if f"{day}:CA:house:1" in contests else conn.execute(
                    "select area_id from polity where iso_code = 'US-CA'"
                ).fetchall()
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=cs.key("us.office", office_key),
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                cs.add(
                    "name", entity_id=contest, text=f"{year} California {TITLES[office]} election", lang="en",
                    kind="official", is_preferred=True,
                )
                cs.add("slug", path=f"us/ca/{day}-general/{office}", entity_id=contest)
            roots: list[tuple[UUID, str]] = []
            for option, kind, label, parent in conn.execute(
                "select id, kind, ballot_label, parent_option_id from contest_option where contest_id = %s", [contest]
            ).fetchall():
                if parent is None and kind in ("candidate", "ticket", "write_in"):
                    roots.append((option, label or ""))
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                if row[0] != result_set:
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=1,
            )
            targets: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for label in rows["label"].unique(maintain_order=True):
                if label in NOT_VOTES - {"Scattering"} or label == "Total":
                    continue
                text = NAMES.get(label, label)
                if label == "Scattering":
                    root = cs.key("us.option", f"{key}:scattering")
                    cs.add("contest_option", id=root, contest_id=contest, kind="others", ballot_label="Scattering")
                    targets[label] = root
                    continue
                found = [option for option, name in roots if plain(name) == plain(text)]
                if len(found) > 1:
                    raise ValueError(f"{key}: {len(found)} options for {label}")
                if found:
                    root = found[0]
                else:
                    person = cs.key(f"{SOURCE}.person", f"CA:{slug(text)}")
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=text, lang="en", kind="official", is_preferred=True)
                    root = cs.key("us.option", f"{key}:{person}")
                    party = PARTIES.get((year, office, label))
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None if party is None else cs.key("us.party", party),
                        ballot_label=text,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=root,
                        role="head",
                        ballot_name=text,
                    )
                    roots.append((root, text))
                targets[label] = root
                totals[root] = int(rows.filter(pl.col("label") == label)["votes"].sum())
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                winner = max(totals, key=totals.__getitem__)
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
            for county, label, votes, number, box in rows.select("county", "label", "votes", "page", "box").iter_rows():
                if label in NOT_VOTES - {"Total", "Scattering"}:
                    continue
                fact = {
                    "result_set_id": result_set,
                    "unit_id": counties[plain(county)],
                    "option_id": None if label == "Total" else targets[label],
                    "channel": 0,
                    "measure": 3 if label == "Total" else 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                page, width, height = pages[(year, number)]
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=page,
                    source_id=None,
                    x0=box[0] / width / 3,
                    y0=box[1] / height / 3,
                    x1=box[2] / width / 3,
                    y1=box[3] / height / 3,
                )
        return cs.write()
