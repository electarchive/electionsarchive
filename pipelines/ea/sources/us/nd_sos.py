import re
import subprocess
from datetime import date
from html import unescape
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nd_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = (
    "https://vip.sos.nd.gov/pdfs/Abstracts%20by%20Year/1990%20through%201998%20Statewide%20Election%20Results/"
    "{}/General%20Election%20{}.pdf"
)
HISTORY = "https://sos.nd.gov/sites/default/files/documents/elections/election-results-pdfs/{}.pdf"
PUBLISHER = "North Dakota Secretary of State"
TITLE = "Official abstracts of votes cast at the general elections"
FILES = {
    1918: "1918-11-05-general",
    1920: "1920-11-02-general",
    1922: "1922-11-07-general",
    1924: "1924-11-04-general",
    1926: "1926-11-02-general",
    1928: "1928-11-06-general",
    1930: "1930-11-04-general",
    1932: "1932-11-08-general",
    1934: "1934-11-06-general",
    1936: "1936-11-03-general",
    1938: "1938-11-08-general",
    1940: "1940-11-05-general",
    1942: "1942-11-03-general",
    1944: "1944-11-07-general",
    1946: "1946-11-05-general",
    1948: "1948-11-02-general",
    1950: "1950-11-07-general",
    1954: "1954-11-02-general",
    1958: "1958-11-04-general",
    1960: "1960-11-08-general",
    1964: "1964-11-03-general",
    1966: "1966-11-08-general",
    1962: "1962-11-06-general",
    1970: "1970-11-03-general",
    1974: "1974-11-05-general",
    1978: "1978-11-07-general",
    1980: "1980-11-04-general",
    1982: "1982-11-02-general",
    1984: "1984-11-06-general",
    1986: "1986-11-04-general",
    1988: "1988-11-08-general",
    1990: "1990-11-06-general",
}
DAYS = {
    1918: date(1918, 11, 5),
    1920: date(1920, 11, 2),
    1922: date(1922, 11, 7),
    1924: date(1924, 11, 4),
    1926: date(1926, 11, 2),
    1928: date(1928, 11, 6),
    1930: date(1930, 11, 4),
    1932: date(1932, 11, 8),
    1934: date(1934, 11, 6),
    1936: date(1936, 11, 3),
    1938: date(1938, 11, 8),
    1940: date(1940, 11, 5),
    1942: date(1942, 11, 3),
    1944: date(1944, 11, 7),
    1946: date(1946, 11, 5),
    1992: date(1992, 11, 3),
    1994: date(1994, 11, 8),
    1996: date(1996, 11, 5),
    1998: date(1998, 11, 3),
    1948: date(1948, 11, 2),
    1950: date(1950, 11, 7),
    1954: date(1954, 11, 2),
    1958: date(1958, 11, 4),
    1960: date(1960, 11, 8),
    1964: date(1964, 11, 3),
    1966: date(1966, 11, 8),
    1962: date(1962, 11, 6),
    1970: date(1970, 11, 3),
    1974: date(1974, 11, 5),
    1978: date(1978, 11, 7),
    1980: date(1980, 11, 4),
    1982: date(1982, 11, 2),
    1984: date(1984, 11, 6),
    1986: date(1986, 11, 4),
    1988: date(1988, 11, 8),
    1990: date(1990, 11, 6),
}
COLUMNS: dict[int, tuple[int, dict[int, tuple[str, str, str | None]]]] = {
    1926: (
        0,
        {
            7: ("lieutenant-governor", "Walter Haddock", "REP"),
            8: ("lieutenant-governor", "A. C. Pagenkopf", "DEM"),
            9: ("secretary-of-state", "Robert Byrne", "REP"),
            10: ("secretary-of-state", "F. L. Walker", "DEM"),
            11: ("secretary-of-state", "Charles G. Johnson", None),
            12: ("auditor", "John Steen", "REP"),
            13: ("auditor", "Ole S. Johnson", "DEM"),
            14: ("auditor", "Allan McManus", None),
            15: ("treasurer", "C. A. Fisher", "REP"),
            16: ("treasurer", "Lillian Lillibridge", "DEM"),
            17: ("treasurer", "E. W. Cart", None),
            18: ("attorney-general", "George F. Shafer", "REP"),
            19: ("attorney-general", "S. L. Nichols", "DEM"),
            20: ("insurance-commissioner", "A. Olsness", "REP"),
            21: ("insurance-commissioner", "W. S. Hooper", "DEM"),
            22: ("insurance-commissioner", "Magnus Snowfield", None),
            23: ("commissioner-of-agriculture", "Joseph A. Kitchen", "REP"),
            24: ("commissioner-of-agriculture", "Charles K. Otto", "DEM"),
            25: ("commissioner-of-agriculture", "D. I. Todd", None),
            26: ("public-service-commissioner", "Fay Harding", "REP"),
            27: ("public-service-commissioner", "W. C. McDonnell", "REP"),
            28: ("public-service-commissioner", "Frank Milhollan", "REP"),
            29: ("public-service-commissioner", "C. H. Boatman", "DEM"),
            30: ("public-service-commissioner", "John A. Helling", "DEM"),
            31: ("public-service-commissioner", "Thomas H. Maloney", "DEM"),
            32: ("public-service-commissioner", "James Wenstrom", None),
            33: ("public-service-commissioner", "Oscar Korsmo", None),
            34: ("public-service-commissioner", "J. A. McGovern", None),
        },
    ),
    1924: (
        0,
        {
            2: ("lieutenant-governor", "Walter Maddock", "REP"),
            3: ("lieutenant-governor", "John E. Paulson", "DEM"),
            4: ("secretary-of-state", "Robert Byrne", "REP"),
            5: ("secretary-of-state", "D. E. Shipley", "DEM"),
            6: ("auditor", "John Steen", "REP"),
            7: ("auditor", "D. C. Poindexter", None),
            8: ("treasurer", "O. A. Fisher", "REP"),
            9: ("treasurer", "J. V. Birder", "DEM"),
            10: ("attorney-general", "George V. Shafer", "REP"),
            11: ("attorney-general", "T. H. H. Thoreson", "DEM"),
            12: ("insurance-commissioner", "S. A. Olsness", "REP"),
            13: ("insurance-commissioner", "W. S. Hooper", "DEM"),
            14: ("commissioner-of-agriculture", "Joseph A. Kitchen", "REP"),
            15: ("commissioner-of-agriculture", "William J. Church", None),
            16: ("public-service-commissioner", "Fay Harding", "REP"),
            17: ("public-service-commissioner", "C. W. McDonnell", "REP"),
            18: ("public-service-commissioner", "Frank Milholland", "REP"),
            19: ("public-service-commissioner", "L. M. Byrne", "DEM"),
            20: ("public-service-commissioner", "Edward Hadley", "DEM"),
            21: ("public-service-commissioner", "Edward Kibler", "DEM"),
        },
    ),
    1922: (
        0,
        {
            4: ("lieutenant-governor", "Frank H. Hyland", "REP"),
            5: ("lieutenant-governor", "Eric A. Bowman", None),
            6: ("secretary-of-state", "Thomas Hall", "REP"),
            7: ("secretary-of-state", "P. A. Moeller", None),
            8: ("auditor", "D. C. Poindexter", "REP"),
            9: ("auditor", "Hans Anderson", "DEM"),
            10: ("treasurer", "John Steen", "REP"),
            11: ("treasurer", "E. G. Larson", None),
            12: ("attorney-general", "George F. Shafer", "REP"),
            13: ("attorney-general", "H. J. Ulsrud", None),
            14: ("insurance-commissioner", "S. A. Olsness", "REP"),
            15: ("insurance-commissioner", "E. A. Lillibridge", "DEM"),
            16: ("commissioner-of-agriculture", "J. A. Kitchen", "REP"),
            17: ("commissioner-of-agriculture", "Bert F. Baker", None),
            18: ("public-service-commissioner", "Fay Harding", "REP"),
            19: ("public-service-commissioner", "C. W. McDonnell", "REP"),
            20: ("public-service-commissioner", "Frank Milholland", "REP"),
            21: ("public-service-commissioner", "Arthur Dixon", "DEM"),
            22: ("public-service-commissioner", "Wm. J. O'Leary", "DEM"),
            23: ("public-service-commissioner", "C. W. Pollock", "DEM"),
        },
    ),
    1920: (
        0,
        {
            4: ("lieutenant-governor", "John F. McGrann", "DEM"),
            5: ("lieutenant-governor", "Howard R. Wood", "REP"),
            6: ("secretary-of-state", "Thomas Hall", "REP"),
            7: ("secretary-of-state", "Alfhild Alfson", None),
            8: ("auditor", "Carl R. Kositzky", "DEM"),
            9: ("auditor", "D. C. Poindexter", "REP"),
            10: ("treasurer", "Ole Kaldor", None),
            11: ("treasurer", "John Steen", "REP"),
            12: ("attorney-general", "Rheinhart J. Kamplin", "DEM"),
            13: ("attorney-general", "William Demise", "REP"),
            14: ("insurance-commissioner", "S. A. Olsness", "REP"),
            15: ("insurance-commissioner", "G. I. Solum", "DEM"),
            16: ("commissioner-of-agriculture", "John N. Hagen", "REP"),
            17: ("commissioner-of-agriculture", "J. T. Nelson", None),
            18: ("public-service-commissioner", "H. D. Allert", "DEM"),
            19: ("public-service-commissioner", "F. C. Hildebrand", None),
            20: ("public-service-commissioner", "C. W. McDonnell", "REP"),
            21: ("public-service-commissioner", "Frank Milholland", "REP"),
            22: ("public-service-commissioner", "F. J. Krueger", "REP"),
            23: ("public-service-commissioner", "W. H. Stutsman", "REP"),
        },
    ),
    1918: (
        0,
        {
            4: ("lieutenant-governor", "Howard R. Wood", "REP"),
            5: ("lieutenant-governor", "J. P. Smith", "DEM"),
            6: ("secretary-of-state", "Thomas Hall", "REP"),
            7: ("auditor", "Carl R. Kositzky", "REP"),
            8: ("auditor", "Theo. Serr", "DEM"),
            9: ("treasurer", "Obert A. Olson", "REP"),
            10: ("treasurer", "M. F. Hegge", "DEM"),
            11: ("attorney-general", "William Langer", "REP"),
            12: ("attorney-general", "G. S. Wooledge", "DEM"),
            13: ("insurance-commissioner", "S. A. Olsness", "REP"),
            14: ("insurance-commissioner", "F. B. Schneller", "DEM"),
            15: ("commissioner-of-agriculture", "John N. Hagan", "REP"),
            16: ("commissioner-of-agriculture", "David Gorman", "DEM"),
            17: ("public-service-commissioner", "S. J. Aandahl", "REP"),
            18: ("public-service-commissioner", "O. F. Dupuis", "REP"),
            19: ("public-service-commissioner", "Frank Milholland", "REP"),
            20: ("public-service-commissioner", "J. L. Harvey", "DEM"),
            21: ("public-service-commissioner", "M. P. Johnson", "DEM"),
            22: ("public-service-commissioner", "Adam G. Lesmeister", "DEM"),
        },
    ),
    1992: (
        1,
        {
            1: ("president", "Bill Clinton", "DEM"),
            2: ("president", "George Bush", "REP"),
            3: ("president", "Ross Perot", "IND"),
            4: ("president", "Lyndon H. LaRouche", "IND"),
            5: ("president", "Andre Marrou", "IND"),
            6: ("president", "John Hagelin", "IND"),
            7: ("president", "James Warren", "IND"),
            8: ("president", "Lenora B. Fulani", "IND"),
            9: ("president", "Earl F. Dodge", "OTH"),
            10: ("senate", "Byron L. Dorgan", "DEM"),
            11: ("senate", "Steve Sydness", "REP"),
            12: ("senate", "Tom Asbridge", "IND"),
            17: ("governor", "Nicholas Spaeth", "DEM"),
            18: ("governor", "Edward T. Schafer", "REP"),
            19: ("governor", "Harley McLain", "IND"),
            20: ("governor", "Michael O. DuPaul", "IND"),
            21: ("secretary-of-state", "Jim Kusler", "DEM"),
            22: ("secretary-of-state", "Alvin A. Jaeger", "REP"),
            23: ("auditor", "Mike Puklich", "DEM"),
            24: ("auditor", 'Robert "Bob" Peterson', "REP"),
            25: ("treasurer", "Kathi Gilmore", "DEM"),
            26: ("treasurer", "Claus H. Lembke", "REP"),
            27: ("attorney-general", "Heidi Heitkamp", "DEM"),
            28: ("attorney-general", 'Warren "Duke" Albrecht', "REP"),
            29: ("insurance-commissioner", "Glenn Pomeroy", "DEM"),
            30: ("insurance-commissioner", "Kent Olson", "REP"),
            31: ("insurance-commissioner", "Jan Shelver", "IND"),
            32: ("commissioner-of-agriculture", "Sarah Vogel", "DEM"),
            33: ("commissioner-of-agriculture", "Bill Bowman", "REP"),
            34: ("commissioner-of-agriculture", "Andy Heinze", "IND"),
            35: ("public-service-commissioner", "Julie Erjavec", "DEM"),
            36: ("public-service-commissioner", "Leo M. Reinbold", "REP"),
            37: ("tax-commissioner", 'Robert E. "Bob" Hanson', "DEM"),
            38: ("tax-commissioner", "Rod Backman", "REP"),
        },
    ),
    1994: (
        0,
        {
            1: ("senate", "Ben Clayburgh", "REP"),
            2: ("senate", "Kent Conrad", "DEM"),
            6: ("public-service-commissioner", "Ward K. Johnson", "REP"),
            7: ("public-service-commissioner", "Bruce Hagen", "DEM"),
            8: ("public-service-commissioner:special", "Susan E. Wefald", "REP"),
            9: ("public-service-commissioner:special", "Julie Hill", "DEM"),
            10: ("commissioner-of-labor", "Craig M. Hagen", None),
            11: ("commissioner-of-labor", "Gary Holm", None),
        },
    ),
    1996: (
        0,
        {
            2: ("president", "Bill Clinton", "DEM"),
            3: ("president", "Bob Dole", "REP"),
            4: ("president", "Harry Browne", "LIB"),
            5: ("president", "John Hagelin", "NAT"),
            6: ("president", "Ross Perot", "REF"),
            7: ("president", "Howard Phillips", "IND"),
            11: ("governor", "Lee Kaldor", "DEM"),
            12: ("governor", "Edward T. Schafer", "REP"),
            13: ("secretary-of-state", "Shelley Seeberg", "DEM"),
            14: ("secretary-of-state", 'Alvin A. "Al" Jaeger', "REP"),
            15: ("auditor", "Shirley Dykshoorn", "DEM"),
            16: ("auditor", "Robert R. (Bob) Peterson", "REP"),
            17: ("treasurer", "Kathi Gilmore", "DEM"),
            18: ("treasurer", "Randy A. Schobinger", "REP"),
            19: ("attorney-general", "Heidi Heitkamp", "DEM"),
            20: ("attorney-general", "Ward K. Johnson", "REP"),
            21: ("insurance-commissioner", "Glenn Pomeroy", "DEM"),
            22: ("insurance-commissioner", "C. H. Lembke", "REP"),
            23: ("insurance-commissioner", "Jan Shelver", "IND"),
            24: ("commissioner-of-agriculture", "Roger Johnson", "DEM"),
            25: ("commissioner-of-agriculture", "Dina Butcher", "REP"),
            26: ("public-service-commissioner", "Steve Tomac", "DEM"),
            27: ("public-service-commissioner", "Susan Wefald", "REP"),
            28: ("tax-commissioner", "Bob Hanson", "DEM"),
            29: ("tax-commissioner", "Rick Clayburgh", "REP"),
        },
    ),
    1944: (
        0,
        {
            18: ("lieutenant-governor", "C. P. Dahl", None),
            19: ("lieutenant-governor", "S. B. Hocking", None),
            20: ("lieutenant-governor", "Oscar W. Hagen", None),
            21: ("lieutenant-governor", "W. H. Northrup", None),
            22: ("secretary-of-state", "Thomas Hall", None),
            23: ("secretary-of-state", "Carl Anderson", None),
            24: ("secretary-of-state", "G. A. Forthun", None),
            25: ("auditor", "Berta E. Baker", None),
            26: ("auditor", "Alice D. Wagner", None),
            27: ("treasurer", "Otto Krueger", None),
            28: ("treasurer", "Julia M. Lynch", None),
            29: ("treasurer", "Vic Gilbreath", None),
            30: ("treasurer", "Karl M. Lundh", None),
            31: ("attorney-general", "Nels G. Johnson", None),
            32: ("attorney-general", "John A. Layne", None),
            33: ("attorney-general", "Olaf M. Thorsen", None),
            34: ("attorney-general", "H. E. Mielke", None),
            35: ("insurance-commissioner", "Oscar E. Erickson", None),
            36: ("insurance-commissioner", "Ed. P. Cosgriff", None),
            37: ("insurance-commissioner", "O. R. Vold", None),
            38: ("commissioner-of-agriculture", "Math Dahl", None),
            39: ("public-service-commissioner", "C. W. McDonnell", None),
        },
    ),
    1940: (
        0,
        {
            14: ("lieutenant-governor", "Oscar W. Hagen", None),
            15: ("lieutenant-governor", "Leslie R. Burgum", None),
            16: ("secretary-of-state", "Herman Thorson", None),
            17: ("secretary-of-state", "Alfred S. Dale", None),
            18: ("auditor", "Berta E. Baker", None),
            19: ("auditor", "Bernhard M. Klinger", None),
            20: ("treasurer", "Carl Anderson", None),
            21: ("treasurer", "Max H. Strehlow", None),
            22: ("attorney-general", "Alvin C. Strutz", None),
            23: ("attorney-general", "Halvor L. Halvorson", None),
            24: ("insurance-commissioner", "Oscar E. Erickson", None),
            25: ("insurance-commissioner", "Ole H. Olson", None),
            26: ("commissioner-of-agriculture", "Math Dahl", None),
            27: ("commissioner-of-agriculture", "A. L. Lindstrom", None),
            28: ("public-service-commissioner", "Ben C. Larkin", None),
            29: ("public-service-commissioner", "C. W. McDonnell", None),
            30: ("public-service-commissioner", "J. C. Costello", None),
            31: ("public-service-commissioner", "Rudolph Raber", None),
            32: ("public-service-commissioner", "Conrad C. Johnson", None),
        },
    ),
    1938: (
        0,
        {
            11: ("lieutenant-governor", "Jack A. Patterson", "REP"),
            12: ("lieutenant-governor", "David G. Kelly", "DEM"),
            13: ("secretary-of-state", "James D. Gronna", "REP"),
            14: ("secretary-of-state", "Rita A. Murphy", "DEM"),
            15: ("auditor", "Berta E. Baker", "REP"),
            16: ("auditor", "Mike Coyne", "DEM"),
            17: ("treasurer", "John R. Omland", "REP"),
            18: ("treasurer", "Adolph Mickelson", "DEM"),
            19: ("attorney-general", "Alvin C. Strutz", "REP"),
            20: ("attorney-general", "William T. DePuy", "DEM"),
            21: ("insurance-commissioner", "Oscar E. Erickson", "REP"),
            22: ("insurance-commissioner", "Clarence E. Omdahl", "DEM"),
            23: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            24: ("commissioner-of-agriculture", "George P. Wolf", "DEM"),
            25: ("public-service-commissioner", "Ben C. Larkin", "REP"),
            26: ("public-service-commissioner", "James S. McGoogy", "DEM"),
        },
    ),
    1936: (
        0,
        {
            14: ("lieutenant-governor", "T. H. H. Thoresen", "REP"),
            15: ("lieutenant-governor", "P. H. Costello", "DEM"),
            16: ("lieutenant-governor", "William Crockett", None),
            17: ("secretary-of-state", "James D. Gronna", "REP"),
            18: ("secretary-of-state", "Mary A. Fenelon", "DEM"),
            19: ("auditor", "Berta E. Baker", "REP"),
            20: ("auditor", "E. A. Lillibridge", "DEM"),
            21: ("treasurer", "John Gray", "REP"),
            22: ("treasurer", "Philip Heiling", "DEM"),
            23: ("attorney-general", "P. O. Sathre", "REP"),
            24: ("attorney-general", "William T. Depuy", "DEM"),
            25: ("attorney-general", "Owen T. Owen", None),
            26: ("insurance-commissioner", "Oscar B. Erickson", "REP"),
            27: ("insurance-commissioner", "S. A. Olsness", "DEM"),
            28: ("commissioner-of-agriculture", "John N. Hagan", "REP"),
            29: ("commissioner-of-agriculture", "Wm. Kane", "DEM"),
            30: ("public-service-commissioner", "S. S. McDonald", "REP"),
            31: ("public-service-commissioner", "M. S. Hyland", "DEM"),
            32: ("public-service-commissioner", "Theo Martell", None),
        },
    ),
    1934: (
        0,
        {
            14: ("lieutenant-governor", "Walter Welford", "REP"),
            15: ("lieutenant-governor", "P. H. Costello", "DEM"),
            16: ("lieutenant-governor", "B. J. Helland", None),
            17: ("secretary-of-state", "James D. Gronna", "REP"),
            18: ("secretary-of-state", "Laura Wahl Pulscher", "DEM"),
            19: ("auditor", "Berta E. Baker", "REP"),
            20: ("auditor", "Walter G. McDonald", "DEM"),
            21: ("treasurer", "John Gray", "REP"),
            22: ("treasurer", "Oscar Nygaard", "DEM"),
            23: ("attorney-general", "P. O. Sathre", "REP"),
            24: ("attorney-general", "John Moses", "DEM"),
            25: ("insurance-commissioner", "Harold Hopton", "REP"),
            26: ("insurance-commissioner", "John P. Jungers", "DEM"),
            27: ("commissioner-of-agriculture", "Theo. Mantell", "REP"),
            28: ("commissioner-of-agriculture", "O. W. Klindworth", "DEM"),
            29: ("commissioner-of-agriculture", "Harry Juul", None),
            30: ("public-service-commissioner", "Elmer Cart", "REP"),
            31: ("public-service-commissioner", "J. R. Kennedy", "DEM"),
            32: ("public-service-commissioner", "Ashbel Ingerson", None),
        },
    ),
    1932: (
        0,
        {
            17: ("lieutenant-governor", "O. H. Olson", "REP"),
            18: ("lieutenant-governor", "R. A. Johnson", "DEM"),
            19: ("lieutenant-governor", "B. J. Helland", None),
            20: ("secretary-of-state", "Robert Byrne", "REP"),
            21: ("secretary-of-state", "Stanley F. Casey", "DEM"),
            22: ("secretary-of-state", "Roy W. Dalziel", None),
            23: ("auditor", "Berta B. Baker", "REP"),
            24: ("auditor", "Grace Hoopes", "DEM"),
            25: ("treasurer", "Alfred S. Dale", "REP"),
            26: ("treasurer", "William M. Schantz", "DEM"),
            27: ("attorney-general", "A. J. Gronna", "REP"),
            28: ("attorney-general", "Scott Cameron", "DEM"),
            29: ("attorney-general", "K. P. Loesch", None),
            30: ("insurance-commissioner", "S. A. Olsness", "REP"),
            31: ("insurance-commissioner", "Perry R. Benner", "DEM"),
            32: ("commissioner-of-agriculture", "John Husby", "REP"),
            33: ("commissioner-of-agriculture", "John Magill", "DEM"),
            34: ("public-service-commissioner", "Ben C. Larkin", "REP"),
            35: ("public-service-commissioner", "Arthur L. Chapman", "DEM"),
        },
    ),
    1930: (
        0,
        {
            3: ("lieutenant-governor", "John W. Carr", "REP"),
            4: ("lieutenant-governor", "L. A. Swanson", "DEM"),
            5: ("lieutenant-governor", "Charles Hill", None),
            6: ("secretary-of-state", "Robert Byrne", "REP"),
            7: ("secretary-of-state", "Laura I. Wahl", "DEM"),
            8: ("secretary-of-state", "K. P. Loesch", None),
            9: ("auditor", "John Steen", "REP"),
            10: ("auditor", "A. C. Pagenkopf", "DEM"),
            11: ("treasurer", "Berta E. Baker", "REP"),
            12: ("treasurer", "Robert W. Allen", "DEM"),
            13: ("attorney-general", "James Morris", "REP"),
            14: ("attorney-general", "Harry Lashkowitz", "DEM"),
            15: ("insurance-commissioner", "S. A. Olsness", "REP"),
            16: ("commissioner-of-agriculture", "Joseph A. Kitchen", "REP"),
            17: ("commissioner-of-agriculture", "W. E. Cooke", None),
            18: ("commissioner-of-agriculture", "James Pearson", None),
            19: ("public-service-commissioner", "C. W. McDonnell", "REP"),
            20: ("public-service-commissioner", "C. J. Kachelhoffer", "DEM"),
        },
    ),
    1928: (
        0,
        {
            6: ("lieutenant-governor", "John W. Carr", "REP"),
            7: ("lieutenant-governor", "E. J. Hughes", "DEM"),
            8: ("secretary-of-state", "Robert Byrne", "REP"),
            9: ("auditor", "John Steen", "REP"),
            10: ("auditor", "W. G. Riebe", "DEM"),
            11: ("treasurer", "Berta E. Baker", "REP"),
            12: ("treasurer", "Kenneth Ferguson", "DEM"),
            13: ("attorney-general", "James Morris", "REP"),
            14: ("attorney-general", "P. W. Lanier", "DEM"),
            15: ("insurance-commissioner", "S. A. Olsness", "REP"),
            16: ("insurance-commissioner", "Luella D. Hildreth", "DEM"),
            17: ("commissioner-of-agriculture", "Joseph A. Kitchen", "REP"),
            18: ("commissioner-of-agriculture", "G. J. Kimball", "DEM"),
            19: ("public-service-commissioner", "Fay Harding", "REP"),
            20: ("public-service-commissioner", "V. P. Cadieux", "DEM"),
        },
    ),
    1942: (
        0,
        {
            9: ("lieutenant-governor", "Quentin N. Burdick", None),
            10: ("lieutenant-governor", "Henry Holt", None),
            11: ("secretary-of-state", "Herman Thorson", None),
            12: ("secretary-of-state", "Thomas Hall", None),
            13: ("auditor", "Berta E. Baker", None),
            14: ("auditor", "Albert H. Haut", None),
            15: ("treasurer", "Carl Anderson", None),
            16: ("treasurer", "John Omland", None),
            17: ("attorney-general", "Alvin C. Strutz", None),
            18: ("attorney-general", "Walter O. Buck", None),
            19: ("insurance-commissioner", "Oscar E. Erickson", None),
            20: ("insurance-commissioner", "John N. Hagan", None),
            21: ("commissioner-of-agriculture", "Math Dahl", None),
            22: ("commissioner-of-agriculture", "C. P. Dahl", None),
            23: ("public-service-commissioner", "S. S. McDonald", None),
            24: ("public-service-commissioner", "George Schuett", None),
        },
    ),
    1946: (
        0,
        {
            10: ("lieutenant-governor", "Quentin Burdick", "DEM"),
            11: ("lieutenant-governor", "C. P. Dahl", "REP"),
            12: ("secretary-of-state", "B. E. Robinson", "DEM"),
            13: ("secretary-of-state", "Thomas Hall", "REP"),
            14: ("auditor", "Vernon B. Hathaway", "DEM"),
            15: ("auditor", "Berta E. Baker", "REP"),
            16: ("treasurer", "Douglas Dahlin", "DEM"),
            17: ("treasurer", "H. W. Swenson", "REP"),
            18: ("attorney-general", "Agnes Molzahn", "DEM"),
            19: ("attorney-general", "Nels G. Johnson", "REP"),
            20: ("insurance-commissioner", "Tom Lynch", "DEM"),
            21: ("insurance-commissioner", "Otto Krueger", "REP"),
            22: ("commissioner-of-agriculture", "Mike Purdy", "DEM"),
            23: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            24: ("public-service-commissioner", "J. Arthur Engen", "DEM"),
            25: ("public-service-commissioner", "Ben C. Larkin", "REP"),
            26: ("public-service-commissioner", "Martin A. Stanley", "DEM"),
        },
    ),
    1948: (
        0,
        {
            7: ("lieutenant-governor", "C. P. Dahl", None),
            8: ("lieutenant-governor", "J. W. Matejcek", None),
            9: ("lieutenant-governor", "Otto Otteson", None),
            10: ("secretary-of-state", "Thomas Hall", None),
            11: ("secretary-of-state", "Ellen F. Mayoue", None),
            12: ("auditor", "Berta E. Baker", None),
            13: ("auditor", "Byron Krantz", None),
            14: ("treasurer", "Albert Jacobson", None),
            15: ("treasurer", "Lawrence Knoke", None),
            16: ("attorney-general", "Wallace E. Warner", None),
            17: ("attorney-general", "Thos. D. Morrow", None),
            18: ("insurance-commissioner", "Otto Krueger", None),
            19: ("insurance-commissioner", "J. Arthur Engen", None),
            20: ("commissioner-of-agriculture", "Math Dahl", None),
            21: ("commissioner-of-agriculture", "J. B. Mertes", None),
            22: ("public-service-commissioner", "Elmer W. Cart", None),
            23: ("public-service-commissioner", "Douglas Dahlin", None),
        },
    ),
    1950: (
        0,
        {
            10: ("lieutenant-governor", "Ray Schnell", "REP"),
            11: ("lieutenant-governor", "Milton K. Higgins", "DEM"),
            12: ("secretary-of-state", "Thomas Hall", "REP"),
            13: ("secretary-of-state", "Ruth B. Herzberg", "DEM"),
            14: ("auditor", "Berta E. Baker", "REP"),
            15: ("treasurer", "Albert Jacobson", "REP"),
            16: ("treasurer", "Loretta Gray", "DEM"),
            17: ("attorney-general", "E. T. Christianson", "REP"),
            18: ("attorney-general", "Richard Boulger", "DEM"),
            19: ("insurance-commissioner", "A. J. Jensen", "REP"),
            20: ("insurance-commissioner", "Henry W. Anderberg", "DEM"),
            21: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            22: ("commissioner-of-agriculture", "Fred Port", "DEM"),
            23: ("public-service-commissioner", "E. H. Brant", "REP"),
            24: ("public-service-commissioner", "Norman B. Jenson", "DEM"),
        },
    ),
    1954: (
        0,
        {
            8: ("lieutenant-governor", "C. P. Dahl", "REP"),
            9: ("lieutenant-governor", "S. B. Hocking", "DEM"),
            10: ("secretary-of-state", "Ben Meier", "REP"),
            11: ("secretary-of-state", "B. Earle Robinson", "DEM"),
            12: ("auditor", "Berta E. Baker", "REP"),
            13: ("auditor", "S. J. Christopherson", "DEM"),
            14: ("treasurer", "Albert Jacobson", "REP"),
            15: ("treasurer", "Ethel Knudsen", "DEM"),
            16: ("attorney-general", "Leslie R. Burgum", "REP"),
            17: ("attorney-general", "S. E. Halpern", "DEM"),
            18: ("insurance-commissioner", "A. J. Jensen", "REP"),
            19: ("insurance-commissioner", "H. N. Jenson", "DEM"),
            20: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            21: ("commissioner-of-agriculture", "William L. Guy", "DEM"),
            22: ("public-service-commissioner", "Anson J. Anderson", "REP"),
            23: ("public-service-commissioner", "Lawrence Knoke", "DEM"),
        },
    ),
    1958: (
        0,
        {
            12: ("lieutenant-governor", "C. P. Dahl", "REP"),
            13: ("lieutenant-governor", "Lavern Schoeder", "DEM"),
            18: ("treasurer", "John R. Erickson", "REP"),
            19: ("treasurer", "Ray Thompson", "DEM"),
        },
    ),
    1960: (
        0,
        {
            12: ("lieutenant-governor", "Orville W. Hagen", "REP"),
            13: ("lieutenant-governor", "Lavern Schoeder", "DEM"),
            14: ("secretary-of-state", "Ben Meier", "REP"),
            15: ("secretary-of-state", "Lloyd B. Omdahl", "DEM"),
            16: ("auditor", "Curtis Olson", "REP"),
            17: ("auditor", "Weldon Haugen", "DEM"),
            18: ("treasurer", "John R. Erickson", "REP"),
            19: ("treasurer", "Albert Heide", "DEM"),
            20: ("attorney-general", "Leslie R. Burgum", "REP"),
            21: ("attorney-general", "Charles Murphy", "DEM"),
            22: ("insurance-commissioner", "A. J. Jensen", "REP"),
            23: ("insurance-commissioner", "Don Sloan", "DEM"),
            24: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            25: ("commissioner-of-agriculture", "Bruce Hagen", "DEM"),
            26: ("public-service-commissioner", "Richard J. Thompson", "REP"),
            27: ("public-service-commissioner", "Ralph Dewing", "DEM"),
        },
    ),
    1962: (
        0,
        {
            10: ("lieutenant-governor", "Frank A. Wenstrom", "REP"),
            11: ("lieutenant-governor", "Leonell W. (Lee) Fraase", "DEM"),
            12: ("secretary-of-state", "Ben Meier", "REP"),
            13: ("secretary-of-state", "Larry Schneider", "DEM"),
            14: ("auditor", "Curtis Olson", "REP"),
            15: ("auditor", "Florence Swenson", "DEM"),
            16: ("treasurer", "Phil Hoghaug", "REP"),
            17: ("treasurer", "Elmer Strand", "DEM"),
            18: ("attorney-general", "Helgi Johanneson", "REP"),
            19: ("attorney-general", "Charles Tighe", "DEM"),
            20: ("insurance-commissioner", "Frank Albers", "REP"),
            21: ("insurance-commissioner", "Hans Walker, Jr.", "DEM"),
            22: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            23: ("commissioner-of-agriculture", "Harold R. Hanson", "DEM"),
            24: ("public-service-commissioner", "Ben J. Wolf", "REP"),
            25: ("public-service-commissioner", "E. Odin Sjaastad", "DEM"),
        },
    ),
    1964: (
        0,
        {
            16: ("lieutenant-governor", "Frank A. Wenstrom", "REP"),
            17: ("lieutenant-governor", "Charles Tighe", "DEM"),
            18: ("secretary-of-state", "Ben Meier", "REP"),
            19: ("secretary-of-state", "Elmer Strand", "DEM"),
            20: ("auditor", "Curtis Olson", "REP"),
            21: ("auditor", "Ted Miller", "DEM"),
            22: ("treasurer", "Phil Hoghaug", "REP"),
            23: ("treasurer", "Walter Christensen", "DEM"),
            24: ("attorney-general", "Helgi Johanneson", "REP"),
            25: ("attorney-general", "Lee W. Fraase", "DEM"),
            26: ("insurance-commissioner", "Ted Weisenburger", "REP"),
            27: ("insurance-commissioner", "K. O. Nygaard", "DEM"),
            28: ("commissioner-of-agriculture", "Arne Dahl", "REP"),
            29: ("commissioner-of-agriculture", "Isak Hystad", "DEM"),
            30: ("public-service-commissioner", "Dean Miller", "REP"),
            31: ("public-service-commissioner", "Bruce Hagen", "DEM"),
        },
    ),
    1966: (
        0,
        {
            6: ("public-service-commissioner", "B. C. Thompson", "DEM"),
            7: ("public-service-commissioner", "Richard Elkin", "REP"),
            8: ("justice-of-the-supreme-court", "William S. Murray", None),
            9: ("justice-of-the-supreme-court", "Wm. L. Paulson", None),
            10: ("commissioner-of-labor", "Earl W. Anderson", None),
            11: ("commissioner-of-labor", "Orville W. Hagen", None),
        },
    ),
    1970: (
        0,
        {
            9: ("public-service-commissioner", "Earl Redlin", "REP"),
            10: ("public-service-commissioner", "Bruce Hagen", "DEM"),
            12: ("commissioner-of-labor", "Orville W. Hagen", None),
            13: ("commissioner-of-labor", "Daryl E. Thompson", None),
        },
    ),
    1974: (
        0,
        {
            6: ("commissioner-of-labor", "Orville W. Hagen", None),
        },
    ),
    1982: (
        0,
        {
            8: ("public-service-commissioner", "Bruce Hagen", "DEM"),
            9: ("public-service-commissioner", "Dale Sandstrom", "REP"),
            18: ("commissioner-of-labor", "Orville W. Hagen", None),
            19: ("commissioner-of-labor", "Byron Knutson", None),
        },
    ),
    1984: (
        0,
        {
            16: ("secretary-of-state", "Claire Edwin Blomquist", "IND"),
            17: ("secretary-of-state", "Bryon Knutson", None),
            18: ("secretary-of-state", "Ben Meier", "REP"),
            19: ("auditor", 'M. K. "Heidi" Heitkamp', "DEM"),
            20: ("auditor", 'Robert "Bob" Peterson', "REP"),
            21: ("treasurer", "Bob Hanson", "DEM"),
            22: ("treasurer", "John Steven Lesmeister", "REP"),
            23: ("attorney-general", 'Nicholas "Nick" Spaeth', "DEM"),
            24: ("attorney-general", "Robert Wefald", "REP"),
            25: ("insurance-commissioner", "Diane Alm", "REP"),
            26: ("insurance-commissioner", "Jan Gerhart-Shelver", "IND"),
            27: ("insurance-commissioner", "Earl Pomeroy", "DEM"),
            28: ("commissioner-of-agriculture", "Kent Jones", "REP"),
            29: ("commissioner-of-agriculture", "Bruce W. Larson", "DEM"),
            30: ("public-service-commissioner", 'S. F. "Buckshot" Hoffner', "DEM"),
            31: ("public-service-commissioner", "Dale Sandstrom", "REP"),
        },
    ),
    1986: (
        0,
        {
            8: ("public-service-commissioner", "Leo M. Reinbold", "REP"),
            9: ("public-service-commissioner", "Jim Kusler", "DEM"),
            30: ("commissioner-of-labor", 'Orville W. "Ike" Hagen', None),
            31: ("commissioner-of-labor", "Byron Knutson", None),
        },
    ),
    1978: (
        0,
        {
            6: ("public-service-commissioner", 'Richard "Dick" Elkin', "REP"),
            7: ("public-service-commissioner", 'Robert E. "Bob" Hanson', "DEM"),
            11: ("commissioner-of-labor", "Orville W. Hagen", None),
        },
    ),
    1980: (
        0,
        {
            23: ("secretary-of-state", "Ben Meier", "REP"),
            24: ("secretary-of-state", "Bob Black", "DEM"),
            25: ("auditor", 'Robert "Bob" Peterson', "REP"),
            26: ("auditor", 'Austin "Jim" Engel', "DEM"),
            27: ("treasurer", "John Steven Lesmeister", "REP"),
            28: ("treasurer", "Bob Hanson", "DEM"),
            29: ("attorney-general", 'Robert "Bob" Wefald', "REP"),
            30: ("attorney-general", "Alice K. Olson", "DEM"),
            31: ("insurance-commissioner", 'J. O. "Bud" Wigen', "REP"),
            32: ("insurance-commissioner", "Byron Knutson", None),
            33: ("commissioner-of-agriculture", "Kent Jones", "REP"),
            34: ("commissioner-of-agriculture", 'S. F. "Buckshot" Hoffner', "DEM"),
            35: ("public-service-commissioner", "Leo Reinbold", "REP"),
            36: ("public-service-commissioner", "Rick Maixner", "DEM"),
        },
    ),
    1988: (
        0,
        {
            18: ("auditor", "Steve Pederson", "DEM"),
            19: ("auditor", 'Robert "Bob" Peterson', "REP"),
            20: ("treasurer", 'Robert E. "Bob" Hanson', "DEM"),
            21: ("treasurer", "Steve Easton", "REP"),
            22: ("attorney-general", 'Nicholas "Nick" Spaeth', "DEM"),
            23: ("attorney-general", "John Gosbee", "REP"),
            24: ("insurance-commissioner", "Earl R. Pomeroy", "DEM"),
            25: ("insurance-commissioner", "Margaret Bushee", "REP"),
            26: ("commissioner-of-agriculture", "Sarah Vogel", "DEM"),
            27: ("commissioner-of-agriculture", "Keith Bjerke", "REP"),
            28: ("commissioner-of-agriculture", "Kent Jones", "IND"),
            29: ("commissioner-of-agriculture", "Harley McLain", "IND"),
            30: ("public-service-commissioner", "Bruce Hagen", "DEM"),
            31: ("public-service-commissioner", "Leslie Volochenko", "REP"),
        },
    ),
    1990: (
        0,
        {
            4: ("public-service-commissioner", "Floyd Stromme", "DEM"),
            5: ("public-service-commissioner", "Dale Sandstrom", "REP"),
            6: ("commissioner-of-labor", "Craig Hagen", None),
            7: ("commissioner-of-labor", 'S. F. "Buckshot" Hoffner', None),
        },
    ),
    1998: (
        1,
        {
            2: ("senate", "Donna Nalewaja", "REP"),
            3: ("senate", "Byron L. Dorgan", "DEM"),
            4: ("senate", "Harley McLain", "REF"),
            8: ("public-service-commissioner", "Leo M. Reinbold", "REP"),
            9: ("public-service-commissioner", "Vern Thompson", "DEM"),
        },
    ),
}
NUMBER = r"[\dIl][\dIl,.?<]*['`’:;]?|(?=.*\d)[\dIlS][\dIlo,.?<]*['`’:;]?"
BANDED = {1964}
EDGES = {1966: 100}
DEBUG: list = []
WORDS: dict = {}
LINES: dict = {}
CUTS = {1928: 870, 1920: 960, 1922: 800}
LABELS = {1924: 400}
PRINTED = {(1926, 7): 127506}
ERRATA = {
    (1940, "Alvin C. Strutz"),
    (1940, "Math Dahl"),
    (1940, "Alfred S. Dale"),
    (1928, "Kenneth Ferguson"),
    (1932, "Berta B. Baker"),
    (1942, "Henry Holt"),
    (1942, "Herman Thorson"),
    (1942, "Berta E. Baker"),
    (1942, "Walter O. Buck"),
    (1942, "C. P. Dahl"),
    (1946, "Thomas Hall"),
    (1944, "Nels G. Johnson"),
    (1944, "Thomas Hall"),
    (1944, "Oscar W. Hagen"),
    (1944, "Olaf M. Thorsen"),
    (1950, "A. J. Jensen"),
    (1966, "B. C. Thompson"),
}
READ_1944 = {("wells", 32): 1386, ("grant", 35): 1606}
TYPOS = {"mclntosh": "mcintosh", "poster": "foster"}
READ_1964: dict[tuple[str, int], int] = {
    ("lamoure", 17): 1713,
    ("mchenry", 17): 2133,
    ("mcintosh", 17): 762,
    ("lamoure", 18): 2021,
    ("mchenry", 18): 2210,
    ("mcintosh", 18): 2061,
    ("lamoure", 20): 2054,
    ("grant", 21): 835,
    ("mchenry", 23): 2192,
    ("sioux", 23): 610,
    ("eddy", 26): 1030,
    ("ransom", 31): 1820,
    ("sargent", 31): 1594,
    ("sheridan", 31): 622,
    ("sioux", 31): 637,
    ("slope", 31): 395,
    ("steele", 31): 1261,
    ("stutsman", 31): 4547,
    ("towner", 31): 1350,
    ("traill", 31): 2236,
    ("walsh", 31): 3682,
    ("ward", 31): 9370,
    ("wells", 31): 2018,
    ("williams", 31): 4765,
}
GAPS_1964 = {("pierce", 16), ("logan", 18), ("cass", 19), ("emmons", 20)}
ALONE = {"Roger Johnson"}
TITLES = {
    "lieutenant-governor": "Lieutenant Governor",
    "secretary-of-state": "Secretary of State",
    "auditor": "State Auditor",
    "treasurer": "State Treasurer",
    "attorney-general": "Attorney General",
    "insurance-commissioner": "Insurance Commissioner",
    "commissioner-of-agriculture": "Agriculture Commissioner",
    "public-service-commissioner": "Public Service Commissioner",
    "tax-commissioner": "Tax Commissioner",
    "commissioner-of-labor": "Labor Commissioner",
    "justice-of-the-supreme-court": "Justice of the Supreme Court",
}


def url(year: int) -> str:
    if year in FILES:
        return HISTORY.format(FILES[year])
    return URL.format(year, DAYS[year].strftime("%m-%d-%Y"))


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def parse() -> None:
    with connect() as conn:
        codes = {
            plain(text): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '38%'"""
            ).fetchall()
        }
    if len(codes) != 53:
        raise ValueError("North Dakota needs 53 counties")
    rows: list[dict[str, object]] = []
    for year, (index, columns) in COLUMNS.items():
        html = subprocess.run(
            ["pdftotext", "-bbox", str(raw.cached(SOURCE, url(year)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        words = [
            (float(a), float(b), float(c), float(d), unescape(t))
            for a, b, c, d, t in re.findall(
                r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<',
                html.split("<page ")[1:][index],
            )
            if float(a) < CUTS.get(year, 1e9)
        ]
        if year < 1940:
            words = [
                (
                    w[0] + (w[2] - w[0]) * sum(map(len, pieces[:i])) / len(w[4]),
                    w[1],
                    w[0] + (w[2] - w[0]) * sum(map(len, pieces[: i + 1])) / len(w[4]),
                    w[3],
                    piece,
                )
                for w in words
                for pieces in [re.split(r"(\|)", w[4])]
                for i, piece in enumerate(pieces)
                if piece and piece != "|"
            ]
        for i, w in enumerate(words):
            if w[4] == "A" and any(v[4] == "dams" and abs(v[1] - w[1]) < 2 and 0 <= v[0] - w[2] < 3 for v in words):
                other = next(v for v in words if v[4] == "dams" and abs(v[1] - w[1]) < 2 and 0 <= v[0] - w[2] < 3)
                words.remove(other)
                words[i] = (w[0], w[1], other[2], other[3], "Adams")
        first = next(w for w in words if w[4].upper() == "ADAMS" and w[0] < LABELS.get(year, 300))
        edge = (
            EDGES.get(year)
            or -20
            + min(w[0] for w in words if abs(w[1] - first[1]) < 3 and w[0] > first[2] and re.fullmatch(NUMBER, w[4]))
            if year in FILES
            else 125
        )
        names = sorted(
            (w for w in words if w[0] < edge and re.match(r"[A-Za-z]", w[4]) and w[3] - w[1] < 12),
            key=lambda w: w[1],
        )
        start = next(i for i, w in enumerate(names) if w[4].upper() == "ADAMS")
        lines: list[list] = []
        for w in names[start:]:
            middle = (w[1] + w[3]) / 2
            if lines and abs(lines[-1][0] - middle) < 2:
                lines[-1][1] += w[4]
                continue
            lines.append([middle, w[4]])
            if w[4].upper().rstrip(".") in {"TOTAL", "TOTALS"}:
                break
        keys = [TYPOS.get(plain(text), plain(text)) for _, text in lines[:-1]]
        if sorted(keys) != sorted(codes):
            raise ValueError(f"{year}: counties {sorted(set(keys) ^ set(codes))}")
        LINES[year] = (lines, edge, keys, min(w[0] for w in names))
        numbers = [w for w in words if re.fullmatch(NUMBER, w[4]) and w[0] >= edge and w[3] - w[1] <= 12]

        def nearest(w: tuple) -> int:
            middle = (w[1] + w[3]) / 2
            return min(range(len(lines)), key=lambda i: abs(lines[i][0] - middle))

        found = [(nearest(w), w) for w in numbers if abs(lines[nearest(w)][0] - (w[1] + w[3]) / 2) <= 3.6]
        totals = sorted(w for line, w in found if line == len(lines) - 1)

        def column_of(w: tuple) -> int:
            middle = (w[0] + w[2]) / 2
            return min(
                range(len(totals)),
                key=lambda i: (
                    0
                    if totals[i][0] - 2 <= middle <= totals[i][2] + 2
                    else abs((totals[i][0] + totals[i][2]) / 2 - middle)
                ),
            )

        def value(text: str) -> int:
            return int(
                re.sub(r"[Il]", "1", text)
                .replace("S", "0")
                .replace("o", "0")
                .replace(",", "")
                .replace(".", "")
                .replace("?", "0")
                .replace("<", "")
                .rstrip("'`’:;")
            )

        cells: dict[tuple[int, int], int] = {}
        unreadable: set[tuple[int, int]] = set()
        if year in BANDED:
            bands: list[list[tuple]] = []
            for w in sorted((w for w in numbers if w[1] > lines[0][0] - 8), key=lambda w: (w[1] + w[3]) / 2):
                if bands and (w[1] + w[3]) / 2 - bands[-1][-1][1] < 2.4:
                    bands[-1].append((w[0], (w[1] + w[3]) / 2, w[2], w[3], w[4]))
                else:
                    bands.append([(w[0], (w[1] + w[3]) / 2, w[2], w[3], w[4])])
            bands = [band for band in bands if len(band) > 3]
            while len(bands) < len(lines):
                widest = max(range(len(bands)), key=lambda i: len(bands[i]))
                members = bands[widest]
                cut = max(range(1, len(members)), key=lambda i: members[i][1] - members[i - 1][1])
                bands[widest : widest + 1] = [members[:cut], members[cut:]]
            if len(bands) != len(lines):
                raise ValueError(f"{year}: {len(bands)} bands for {len(lines)} rows")
            for band in bands[-1:]:
                band.sort()
                merged = [band[0]]
                for w in band[1:]:
                    if len(w[4]) == 1 and -3 <= w[0] - merged[-1][2] < 5 and abs(w[1] - merged[-1][1]) < 1.5:
                        merged[-1] = (merged[-1][0], merged[-1][1], w[2], w[3], merged[-1][4] + w[4])
                    else:
                        merged.append(w)
                band[:] = merged
            totals = sorted(bands[-1])
            placed: dict[tuple[int, int], tuple] = {}
            for line, band in enumerate(bands):
                for w in band:
                    column = column_of(w)
                    middle = sorted(x[1] for x in band)[len(band) // 2]
                    if (line, column) in placed and abs(placed[(line, column)][1] - middle) <= abs(w[1] - middle):
                        continue
                    placed[(line, column)] = w
                    cells[(line, column)] = value(w[4])
                    unreadable.discard((line, column))
                    if "?" in w[4] or re.search(r",\d{1,2}$", w[4]):
                        unreadable.add((line, column))
            for (county, column), amount in READ_1964.items():
                cells[(keys.index(county), column)] = amount
            for county, column in GAPS_1964:
                cells[(keys.index(county), column)] = 0
                unreadable.add((keys.index(county), column))
            unreadable -= {(keys.index(county), column) for county, column in READ_1964}
        elif year in FILES:
            bycolumn: dict[int, list[tuple]] = {}
            for w in numbers:
                bycolumn.setdefault(column_of(w), []).append(w)
            for column, members in bycolumn.items():
                members.sort(key=lambda w: w[1])
                ys = [(w[1] + w[3]) / 2 for w in members]
                shifts = sorted(y - lines[nearest(w)][0] for y, w in zip(ys, members, strict=True))
                shift = shifts[len(shifts) // 2]
                rows_y = [line[0] + shift for line in lines]
                inf = float("inf")
                cost = [[inf] * (len(rows_y) + 1) for _ in range(len(ys) + 1)]
                back: dict[tuple[int, int], tuple[int, int, int | None]] = {}
                cost[0][0] = 0.0
                for i in range(len(ys) + 1):
                    for j in range(len(rows_y) + 1):
                        if cost[i][j] == inf:
                            continue
                        options = []
                        if i < len(ys):
                            options.append((i + 1, j, cost[i][j] + 8.0, None))
                        if j < len(rows_y):
                            options.append((i, j + 1, cost[i][j] + 3.0, None))
                        if i < len(ys) and j < len(rows_y):
                            options.append((i + 1, j + 1, cost[i][j] + abs(ys[i] - rows_y[j]), j))
                        for ni, nj, c, matched in options:
                            if c < cost[ni][nj]:
                                cost[ni][nj] = c
                                back[(ni, nj)] = (i, j, matched)
                i, j = len(ys), len(rows_y)
                while (i, j) in back:
                    pi, pj, matched = back[(i, j)]
                    if matched is not None:
                        cells[(matched, column)] = value(members[pi][4])
                        WORDS[(year, matched, column)] = (index, members[pi])
                        if re.search(r"[?<So]", members[pi][4]) or re.search(r",\d{1,2}$", members[pi][4]):
                            unreadable.add((matched, column))
                    i, j = pi, pj
            if year == 1944:
                for (county, column), amount in READ_1944.items():
                    cells[(keys.index(county), column)] = amount
            if year == 1946:
                cells[(keys.index("richland"), 17)] = 3503
        else:
            for line, w in found:
                column = column_of(w)
                if (line, column) in cells and column in columns:
                    raise ValueError(f"{year}: two numbers in {keys[line]} column {column}")
                cells[(line, column)] = value(w[4])
        for column, (office, candidate, code) in columns.items():
            printed = PRINTED.get((year, column)) or cells[(len(keys), column)]
            gaps = [
                line
                for line in range(len(keys))
                if (line, column) in unreadable or year < 1940 and (line, column) not in cells
            ]
            if len(gaps) == 1:
                cells[(gaps[0], column)] = 0
                cells[(gaps[0], column)] = printed - sum(cells.get((line, column), 0) for line in range(len(keys)))
            values = [cells.get((line, column), 0) for line in range(len(keys))]
            if sum(values) != printed:
                if __import__("os").environ.get("ND_DEBUG"):
                    DEBUG.append(
                        (year, column, office, candidate, sum(values) - printed, list(keys), list(values), printed)
                    )
                    continue
                if (
                    year not in FILES
                    or (year, candidate) not in ERRATA
                    and abs(sum(values) - printed) > max(20, printed // 100)
                ):
                    raise ValueError(f"{year} {candidate}: {sum(values)}, printed {printed}")
                print(f"{year} {candidate}: counties sum to {sum(values)}, printed total {printed}")
            rows.extend(
                {
                    "day": DAYS[year],
                    "office": office,
                    "code": code,
                    "candidate": candidate,
                    "county": codes[key],
                    "votes": value,
                }
                for key, value in zip(keys, values, strict=True)
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Dakota's president, U.S. Senate, governor, and other statewide offices by county "
            "1928–1998 from the Secretary of State's official abstracts",
            polity="US",
        )

        def surname(text: str) -> str:
            words = [word for word in text.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        def parts(text: str) -> list[str]:
            words = [plain(word) for word in re.sub(r'"[^"]*"|\([^)]*\)', " ", text).split()]
            return [word for word in words if word and word not in {"jr", "sr", "ii", "iii", "iv", "dr"}]

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.nd.gov/elections/election-results",
            license="Public record",
        )
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '38%%'"
            ).fetchall()
        )
        if len(counties) != 53:
            raise ValueError("North Dakota needs 53 counties")
        dakota = cs.polity("US-ND")
        ((state,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-ND'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, list[int], str]] = {}
        for person, text, statewide, known, year in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':ND:[a-z-]+$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}.*(:ND:|:president$)' and x.value !~ ':ND:38\\d{3}:'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if not text.split():
                continue
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            ranks[person] = (statewide, known, year, text)
        chosen: dict[str, UUID] = {}

        def fits(one: UUID, candidate: str, year: int) -> bool:
            if candidate in ALONE or min((abs(other - year) for other in ranks[one][2]), default=0) > 30:
                return False
            mine, theirs = parts(candidate), parts(ranks[one][3])
            return len(mine) < 3 or len(theirs) < 3 or mine[1][:1] == theirs[1][:1]

        fptp = cs.key("us.electoral_system", "fptp")
        parties = {
            "DEM": cs.key("us.party", "democratic-party"),
            "REP": cs.key("us.party", "republican-party"),
            "LIB": cs.key("us.party", "libertarian-party"),
            "NAT": cs.key("us.party", "natural-law-party"),
            "REF": cs.key("us.party", "reform-party"),
        }
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':ND:'")
        )
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election' and value ~ ':ND$'")
        )
        offices = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        made: set[UUID] = set()
        for (day, office), lines in frame.sort("day").group_by("day", "office", maintain_order=True):
            year = day.year
            key = f"{day}:ND:{office}"
            election = elections[f"{day}:ND"]
            contest = contests.get(key)
            if contest is None:
                if office in ("president", "senate", "governor"):
                    raise ValueError(f"{key}: no contest on file")
                contest = cs.key("us.contest", key)
                contests[key] = contest
                base = office.removesuffix(":special")
                office_key = f"{base}:ND"
                office_id = cs.key("us.office", office_key)
                if office_key not in offices:
                    offices.add(office_key)
                    cs.add("office", id=office_id, polity_id=dakota, kind="other")
                    name(office_id, f"North Dakota {TITLES[base]}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office_id,
                    district_area_id=state,
                    term="remainder" if office.endswith(":special") else "full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(
                    contest,
                    f"{year} North Dakota {TITLES[base]} election" + (" (unexpired term)" if base != office else ""),
                )
                cs.add("slug", path=f"us/nd/{day}-general/{office.replace(':', '-')}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket', 'write_in')""",
                [contest],
            ).fetchall()
            columns = (
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
            )
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
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
                lines.group_by("candidate", "code", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                matches = [option for option, _, text in options if (text or "") == candidate] or [
                    option for option, _, text in options if surname(text or "") == surname(candidate)
                ]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if matches:
                    option = matches[0]
                elif code == "OTH":
                    option = cs.key("us.option", f"{key}:write-in:{slug(candidate)}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label=candidate)
                else:
                    known = {
                        one
                        for one in people.get(plain(candidate))
                        or people.get(f"{plain(parts(candidate)[0])}|{surname(candidate)}", set())
                        if one in made or fits(one, candidate, year)
                    }
                    person = chosen.get(plain(candidate)) or (
                        max(
                            known,
                            key=lambda one: (
                                ranks.get(one, (False, False, [], ""))[0],
                                ranks.get(one, (False, False, [], ""))[1],
                                -min(
                                    (abs(other - year) for other in ranks.get(one, (False, False, [year], ""))[2]),
                                    default=0,
                                ),
                            ),
                        )
                        if known
                        else cs.key(f"{SOURCE}.person", f"ND:{slug(candidate)}")
                    )
                    chosen[plain(candidate)] = person
                    if person in cs.minted and person not in made:
                        made.add(person)
                        people.setdefault(plain(candidate), set()).add(person)
                        people.setdefault(f"{plain(parts(candidate)[0])}|{surname(candidate)}", set()).add(person)
                        cs.add("person", id=person)
                        name(person, candidate)
                    elif person not in made:
                        print(f"{key}: {candidate} is joined to {ranks[person][3]}")
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket" if office == "president" else "candidate",
                        party_id=parties.get(code or ""),
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=code == "IND",
                    )
                if option in totals:
                    raise ValueError(f"{key}: two columns share an option with {candidate}")
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {candidate} has {votes}, {on_file[option]} on file")
                totals[option] = votes
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
                is_default=True,
                completeness=1,
            )
            for county, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
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
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
        return cs.write()
