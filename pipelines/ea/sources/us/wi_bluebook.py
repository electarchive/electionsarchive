import csv
import difflib
import io
import itertools
import json
import re
import statistics
import subprocess
from collections import Counter
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image, ImageChops, ImageFilter, ImageOps

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wi_bluebook"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Wisconsin Legislative Reference Bureau"
PAGES = "https://archive.org/download/{0}/{0}_page_numbers.json"
IMAGE = "https://archive.org/download/{}/page/n{}.jpg"
BOOKS = {
    "1981-1982": "micro_IA40706910_0093",
    "1983-1984": "micro_IA40706917_0074",
    "1985-1986": "micro_IA40706923_0147",
    "1987-1988": "micro_IA40706929_0409",
    "1989-1990": "micro_IA40706936_0250",
    "1991-1992": "micro_IA40706939_0451",
    "1993-1994": "micro_IA40706946_0362",
}
WIDTHS = {
    ("1981-1982", 923): 6,
    ("1981-1982", 924): 7,
    ("1981-1982", 925): 3,
    ("1981-1982", 926): 5,
    ("1981-1982", 927): 5,
    ("1981-1982", 928): 5,
    ("1983-1984", 919): 5,
    ("1983-1984", 920): 4,
    ("1983-1984", 921): 7,
    ("1983-1984", 922): 5,
    ("1985-1986", 935): 5,
    ("1985-1986", 936): 5,
    ("1987-1988", 919): 5,
    ("1987-1988", 920): 3,
    ("1987-1988", 921): 6,
    ("1987-1988", 922): 5,
    ("1989-1990", 940): 7,
    ("1989-1990", 941): 5,
    ("1991-1992", 917): 4,
    ("1991-1992", 918): 7,
    ("1993-1994", 938): 7,
    ("1993-1994", 939): 7,
    ("1993-1994", 940): 7,
    ("1981-1982", 907): 7,
    ("1983-1984", 895): 7,
    ("1983-1984", 897): 7,
    ("1983-1984", 898): 5,
    ("1983-1984", 899): 5,
    ("1983-1984", 900): 6,
    ("1987-1988", 897): 4,
    ("1987-1988", 898): 4,
    ("1987-1988", 901): 4,
    ("1987-1988", 902): 3,
    ("1987-1988", 903): 4,
    ("1987-1988", 904): 5,
    ("1989-1990", 927): 8,
    ("1991-1992", 896): 4,
    ("1991-1992", 897): 3,
    ("1991-1992", 899): 3,
    ("1991-1992", 900): 4,
    ("1991-1992", 901): 4,
    ("1993-1994", 921): 8,
}
TABLES = [
    (
        "1978-11-07",
        "governor",
        [("1981-1982", 923, 0)],
        [
            ("CONS", "Eugene R. Zimmerman"),
            ("DEM", "Martin J. Schreiber"),
            ("REP", "Lee S. Dreyfus"),
            ("IND", "John C. Doherty"),
            ("IND", "Adrienne Kaplan"),
            ("IND", "Henry A. Ochsner"),
        ],
    ),
    (
        "1978-11-07",
        "secretary-of-state",
        [("1981-1982", 924, 0)],
        [("CONS", "George A. Meyers"), ("DEM", "Vel Phillips"), ("REP", "Frederic A. Seefeldt")],
    ),
    (
        "1978-11-07",
        "treasurer",
        [("1981-1982", 924, 3)],
        [
            ("CONS", "Katharine J. Keene"),
            ("DEM", "Charles P. Smith"),
            ("REP", "Harold W. Clemens"),
            ("IND", "Fred B. Blair"),
        ],
    ),
    (
        "1978-11-07",
        "attorney-general",
        [("1981-1982", 925, 0)],
        [("CONS", "Thomas J. Bergen"), ("DEM", "Bronson C. La Follette"), ("REP", "William A. Mattka")],
    ),
    (
        "1980-11-04",
        "president",
        [("1981-1982", 926, 0), ("1981-1982", 927, 0)],
        [
            ("CONS", "John Rarick"),
            ("DEM", "Jimmy Carter"),
            ("LIB", "Ed Clark"),
            ("REP", "Ronald Reagan"),
            ("IND", "John B. Anderson"),
            ("IND", "David McReynolds"),
            ("IND", "Clifton DeBerry"),
            ("IND", "Gus Hall"),
            ("IND", "Barry Commoner"),
            ("IND", "Deirdre Griswold"),
        ],
    ),
    (
        "1980-11-04",
        "senate",
        [("1981-1982", 928, 0)],
        [
            ("CONS", "James P. Wickstrom"),
            ("DEM", "Gaylord A. Nelson"),
            ("LIB", "Bervin J. Larson"),
            ("REP", "Robert W. Kasten Jr."),
            ("IND", "Susan L. Hagen"),
        ],
    ),
    (
        "1982-11-02",
        "governor",
        [("1983-1984", 919, 0)],
        [
            ("CONS", "James P. Wickstrom"),
            ("DEM", "Anthony S. Earl"),
            ("LIB", "Larry Smiley"),
            ("REP", "Terry J. Kohler"),
            ("IND", "Peter Seidman"),
        ],
    ),
    (
        "1982-11-02",
        "secretary-of-state",
        [("1983-1984", 920, 0)],
        [
            ("CONS", "David E. Mrotek"),
            ("DEM", "Douglas La Follette"),
            ("LIB", "Leslie G. Key"),
            ("REP", "Frederick H. Rice"),
        ],
    ),
    (
        "1982-11-02",
        "treasurer",
        [("1983-1984", 921, 0)],
        [
            ("CONS", "Alfred E. Feustel"),
            ("DEM", "Charles P. Smith"),
            ("LIB", "Thomas P. Westgaard"),
            ("REP", "Harold W. Clemens"),
        ],
    ),
    (
        "1982-11-02",
        "attorney-general",
        [("1983-1984", 921, 4)],
        [("CONS", "Gene D. Linehan"), ("DEM", "Bronson C. La Follette"), ("LIB", "James S. Hoffert")],
    ),
    (
        "1982-11-02",
        "senate",
        [("1983-1984", 922, 0)],
        [
            ("CONS", "Sanford G. Knapp"),
            ("DEM", "William Proxmire"),
            ("LIB", "George Liljenfeldt"),
            ("REP", "Scott McCallum"),
            ("IND", "William Osborne Hart"),
        ],
    ),
    (
        "1984-11-06",
        "president",
        [("1985-1986", 935, 0), ("1985-1986", 936, 0)],
        [
            ("CONS", "Bob Richards"),
            ("DEM", "Walter F. Mondale"),
            ("LIB", "David Bergland"),
            ("REP", "Ronald Reagan"),
            ("IND", "Sonia Johnson"),
            ("IND", "Gus Hall"),
            ("IND", "Lyndon H. LaRouche Jr."),
            ("IND", "Melvin T. Mason"),
            ("IND", "Dennis L. Serrette"),
            ("IND", "Larry Holmes"),
        ],
    ),
    (
        "1986-11-04",
        "governor",
        [("1987-1988", 919, 0)],
        [
            ("DEM", "Anthony S. Earl"),
            ("LF", "Kathryn A. Christensen"),
            ("REP", "Tommy G. Thompson"),
            ("IND", "Darold E. Wall"),
            ("IND", "Sanford Knapp"),
        ],
    ),
    (
        "1986-11-04",
        "secretary-of-state",
        [("1987-1988", 920, 0)],
        [("DEM", "Douglas La Follette"), ("LF", "Richard L. Ackley"), ("REP", "Clifford W. Krueger")],
    ),
    (
        "1986-11-04",
        "treasurer",
        [("1987-1988", 921, 0)],
        [("DEM", "Charles P. Smith"), ("LF", "Joan Hollingsworth"), ("REP", "William D. Lorge")],
    ),
    (
        "1986-11-04",
        "attorney-general",
        [("1987-1988", 921, 3)],
        [("DEM", "Bronson C. La Follette"), ("LF", "Dennis L. Boyer"), ("REP", "Donald J. Hanaway")],
    ),
    (
        "1986-11-04",
        "senate",
        [("1987-1988", 922, 0)],
        [
            ("DEM", "Edward R. Garvey"),
            ("REP", "Robert W. Kasten Jr."),
            ("SWP", "Margo Storsteen"),
            ("THIRD", "Eugene A. Hem"),
            ("IND", "Peter Y. Taylor Sr."),
        ],
    ),
    (
        "1988-11-08",
        "president",
        [("1989-1990", 940, 0)],
        [
            ("DEM", "Michael Dukakis"),
            ("REP", "George Bush"),
            ("IND", "Ron Paul"),
            ("IND", "Lyndon H. LaRouche"),
            ("IND", "Lenora B. Fulani"),
            ("IND", "David E. Duke"),
            ("IND", "James Warren"),
        ],
    ),
    (
        "1988-11-08",
        "senate",
        [("1989-1990", 941, 0)],
        [
            ("DEM", "Herbert H. Kohl"),
            ("REP", "Susan Engeleiter"),
            ("IND", "George W. Zaehringer"),
            ("IND", "Patricia Grogan"),
            ("IND", "Arlyn F. Wollenburg"),
        ],
    ),
    (
        "1990-11-06",
        "governor",
        [("1991-1992", 917, 0)],
        [("DEM", "Thomas A. Loftus"), ("REP", "Tommy G. Thompson")],
    ),
    (
        "1990-11-06",
        "secretary-of-state",
        [("1991-1992", 917, 2)],
        [("DEM", "Douglas La Follette"), ("REP", "Robert M. Thompson")],
    ),
    (
        "1990-11-06",
        "attorney-general",
        [("1991-1992", 918, 0)],
        [("DEM", "James E. Doyle"), ("LF", "Patricia K. Hammel"), ("REP", "Donald J. Hanaway")],
    ),
    (
        "1990-11-06",
        "treasurer",
        [("1991-1992", 918, 3)],
        [
            ("DEM", "Charles P. Smith"),
            ("LF", "Lucille Berrien"),
            ("REP", "Cathy S. Zeuske"),
            ("IND", "David A. Ameringer"),
        ],
    ),
    (
        "1992-11-03",
        "senate",
        [("1993-1994", 938, 0)],
        [
            ("DEM", "Russell D. Feingold"),
            ("LIB", "William Bittner"),
            ("REP", "Robert W. Kasten Jr."),
            ("IND", "Patrick W. Johnson"),
            ("IND", "Robert L. Kundert"),
            ("IND", "Joseph Selliken"),
            ("IND", "Mervin A. Hanson Sr."),
        ],
    ),
    (
        "1992-11-03",
        "president",
        [("1993-1994", 939, 0), ("1993-1994", 940, 0)],
        [
            ("DEM", "Bill Clinton"),
            ("LF", "Ron Daniels"),
            ("LIB", "Andre Marrou"),
            ("NL", "John Hagelin"),
            ("REP", "George Bush"),
            ("THIRD", "Eugene A. Hem"),
            ("IND", "James Gritz"),
            ("IND", "Jack Herer"),
            ("IND", "Ross Perot"),
            ("IND", "Lyndon H. LaRouche Jr."),
            ("IND", "Lenora B. Fulani"),
            ("IND", "J. Quinn Brisben"),
            ("IND", "James Warren"),
            ("IND", "Howard Phillips"),
        ],
    ),
    (
        "1980-09-09",
        "senate",
        [("1981-1982", 907, 0)],
        [
            ("CONS", "James P. Wickstrom"),
            ("DEM", "Gaylord A. Nelson"),
            ("LIB", "Bervin J. Larson"),
            ("REP", "Douglass Cofrin"),
            ("REP", "Robert W. Kasten Jr."),
            ("REP", "Terry J. Kohler"),
            ("REP", "Russell A. Olson"),
        ],
    ),
    (
        "1982-09-14",
        "governor",
        [("1983-1984", 895, 0)],
        [
            ("CONS", "James P. Wickstrom"),
            ("DEM", "Anthony S. Earl"),
            ("DEM", "Martin J. Schreiber"),
            ("DEM", "James B. Wood"),
            ("LIB", "Larry Smiley"),
            ("REP", "Lowell B. Jackson"),
            ("REP", "Terry J. Kohler"),
        ],
    ),
    (
        "1982-09-14",
        "secretary-of-state",
        [("1983-1984", 897, 0)],
        [
            ("CONS", "David E. Mrotek"),
            ("DEM", "Ada Deer"),
            ("DEM", "Douglas La Follette"),
            ("DEM", "Lewis T. Mittness"),
            ("DEM", "Vel Phillips"),
            ("LIB", "Leslie G. Key"),
            ("REP", "Frederick H. Rice"),
        ],
    ),
    (
        "1982-09-14",
        "treasurer",
        [("1983-1984", 898, 0)],
        [
            ("CONS", "Alfred E. Feustel"),
            ("DEM", "Charles P. Smith"),
            ("LIB", "Thomas P. Westgaard"),
            ("REP", "Harold W. Clemens"),
            ("REP", "Marlene A. Cummings"),
        ],
    ),
    (
        "1982-09-14",
        "attorney-general",
        [("1983-1984", 899, 0)],
        [
            ("CONS", "Gene D. Linehan"),
            ("DEM", "Bronson C. La Follette"),
            ("LIB", "James S. Hoffert"),
            ("REP", "William Belter"),
            ("REP", "Marcus Gumz"),
        ],
    ),
    (
        "1982-09-14",
        "senate",
        [("1983-1984", 900, 0)],
        [
            ("CONS", "Sanford G. Knapp"),
            ("DEM", "Marcel Dandeneau"),
            ("DEM", "William Proxmire"),
            ("LIB", "George Liljenfeldt"),
            ("REP", "Paul Thomas Brewer"),
            ("REP", "Scott McCallum"),
        ],
    ),
    (
        "1986-09-09",
        "governor",
        [("1987-1988", 897, 0), ("1987-1988", 898, 0)],
        [
            ("DEM", "Anthony S. Earl"),
            ("DEM", "Edmond Hou-Seye"),
            ("LF", "Kathryn A. Christensen"),
            ("REP", "Jonathan B. Barry"),
            ("REP", "Joseph A. Ortiz"),
            ("REP", "Tommy G. Thompson"),
            ("REP", "George Watts"),
            ("REP", "Albert Lee Wiley Jr."),
        ],
    ),
    (
        "1986-09-09",
        "secretary-of-state",
        [("1987-1988", 901, 0)],
        [
            ("DEM", "Douglas La Follette"),
            ("LF", "Richard L. Ackley"),
            ("REP", "Thomas Coaty"),
            ("REP", "Clifford W. Krueger"),
        ],
    ),
    (
        "1986-09-09",
        "treasurer",
        [("1987-1988", 902, 0)],
        [
            ("DEM", "Charles P. Smith"),
            ("LF", "Joan Hollingsworth"),
            ("REP", "William D. Lorge"),
        ],
    ),
    (
        "1986-09-09",
        "attorney-general",
        [("1987-1988", 903, 0)],
        [
            ("DEM", "Bronson C. La Follette"),
            ("LF", "Dennis L. Boyer"),
            ("REP", "Donald J. Hanaway"),
            ("REP", "Bartley G. Mauch"),
        ],
    ),
    (
        "1986-09-09",
        "senate",
        [("1987-1988", 904, 0)],
        [
            ("DEM", "Roman R. Blenski"),
            ("DEM", "Matthew J. Flynn"),
            ("DEM", "Edward R. Garvey"),
            ("DEM", "Gary R. George"),
            ("REP", "Robert W. Kasten Jr."),
        ],
    ),
    (
        "1988-09-13",
        "senate",
        [("1989-1990", 927, 0)],
        [
            ("DEM", "Anthony S. Earl"),
            ("DEM", "Edward R. Garvey"),
            ("DEM", "Edmond Hou-Seye"),
            ("DEM", "Herbert H. Kohl"),
            ("DEM", "Douglas La Follette"),
            ("REP", "Susan Engeleiter"),
            ("REP", "Stephen B. King"),
            ("REP", "Peter Y. Taylor Sr."),
        ],
    ),
    (
        "1990-09-11",
        "governor",
        [("1991-1992", 896, 0), ("1991-1992", 897, 0)],
        [
            ("DEM", "Thomas Loftus"),
            ("LF", "Rickey D. Keilholtz"),
            ("REP", "Michele Ann Gess"),
            ("REP", "Edmond Hou-Seye"),
            ("REP", "Willie G. Lovelace"),
            ("REP", "Bennett A. Masel"),
            ("REP", "Tommy G. Thompson"),
        ],
    ),
    (
        "1990-09-11",
        "secretary-of-state",
        [("1991-1992", 899, 0)],
        [
            ("DEM", "Douglas La Follette"),
            ("DEM", "Stuart Levitan"),
            ("REP", "Robert M. Thompson"),
        ],
    ),
    (
        "1990-09-11",
        "treasurer",
        [("1991-1992", 900, 0)],
        [
            ("DEM", "Charles P. Smith"),
            ("LF", "Lucille Berrien"),
            ("REP", "Cathy S. Zeuske"),
        ],
    ),
    (
        "1990-09-11",
        "attorney-general",
        [("1991-1992", 901, 0)],
        [
            ("DEM", "James E. Doyle"),
            ("DEM", "William P. Te Winkle"),
            ("LF", "Patricia K. Hammel"),
            ("REP", "Donald J. Hanaway"),
        ],
    ),
    (
        "1992-09-08",
        "senate",
        [("1993-1994", 921, 0)],
        [
            ("DEM", "Joseph Checota"),
            ("DEM", "Russell D. Feingold"),
            ("DEM", "Edmond E. Hou-Seye"),
            ("DEM", "Thomas Keller"),
            ("DEM", "Jim Moody"),
            ("LIB", "William Bittner"),
            ("REP", "Roger W. Faulkner"),
            ("REP", "Robert W. Kasten Jr."),
        ],
    ),
]
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "CONS": "constitution-party",
    "LF": "WI:labor-farm",
    "SWP": "socialist-workers-party",
    "THIRD": "WI:third-party",
    "NL": "natural-law-party",
    "IND": None,
}
TITLES = {
    "president": "U.S. presidential election in Wisconsin",
    "senate": "U.S. Senate election in Wisconsin",
    "governor": "Wisconsin gubernatorial election",
    "secretary-of-state": "Wisconsin Secretary of State election",
    "treasurer": "Wisconsin State Treasurer election",
    "attorney-general": "Wisconsin Attorney General election",
}
CHECKED: set[tuple[str, str, int]] = {
    ("1986-09-09", "governor", 3),
    ("1982-09-14", "senate", 0),
    ("1982-09-14", "treasurer", 4),
    ("1982-09-14", "secretary-of-state", 6),
    ("1980-09-09", "senate", 4),
    ("1980-09-09", "senate", 1),
    ("1984-11-06", "president", 7),
    ("1984-11-06", "president", 3),
    ("1984-11-06", "president", 1),
}
NOTES = [
    (
        "1992-09-08",
        "senate",
        (
            "A scratch on the Blue Book scan crosses William Bittner's Brown, Buffalo, Clark, and Columbia figures; "
            "they are read as 36, 0, 10, and 1, which fits the visible strokes and his printed total."
        ),
    ),
    (
        "1980-09-09",
        "senate",
        (
            "Gaylord A. Nelson's Barron figure is damaged in the Blue Book and read as 956; it would need to be 866 "
            "for his county figures to match the printed total."
        ),
    ),
    (
        "1984-11-06",
        "president",
        (
            "The Blue Book's statewide totals include absentee ballots counted later under a court order, "
            "which its county figures do not."
        ),
    ),
    (
        "1978-11-07",
        "treasurer",
        (
            "The last digits of Harold W. Clemens's Polk and Portage figures are illegible in the Blue Book; "
            "they are read as 3,579 and 7,201, which match the printed total."
        ),
    ),
    (
        "1982-11-02",
        "attorney-general",
        (
            "The Blue Book notes write-in votes for Marcus Gumz in Columbia (50), Green (9), Marquette (11), and Sauk (101) "
            "counties only; they are not included here."
        ),
    ),
]
FIXES: dict[tuple[str, str, str, int], int] = {
    ("1992-09-08", "senate", "Columbia", 5): 1,
    ("1992-09-08", "senate", "Clark", 5): 10,
    ("1992-09-08", "senate", "Buffalo", 5): 0,
    ("1992-09-08", "senate", "Brown", 5): 36,
    ("1992-09-08", "senate", "Waupaca", 3): 27,
    ("1992-09-08", "senate", "Dunn", 3): 111,
    ("1990-09-11", "governor", "Ozaukee", 4): 0,
    ("1990-09-11", "governor", "Monroe", 4): 0,
    ("1988-09-13", "senate", "Ashland", 7): 72,
    ("1988-09-13", "senate", "Crawford", 4): 73,
    ("1988-09-13", "senate", "Crawford", 1): 172,
    ("1986-09-09", "senate", "Vilas", 3): 75,
    ("1986-09-09", "senate", "Wood", 1): 717,
    ("1986-09-09", "senate", "Pierce", 1): 532,
    ("1986-09-09", "senate", "Pierce", 0): 83,
    ("1986-09-09", "senate", "Dunn", 0): 76,
    ("1986-09-09", "senate", "Grant", 0): 26,
    ("1986-09-09", "attorney-general", "Vilas", 2): 717,
    ("1986-09-09", "governor", "Rock", 7): 285,
    ("1986-09-09", "governor", "Oneida", 7): 75,
    ("1986-09-09", "governor", "Waushara", 4): 22,
    ("1986-09-09", "governor", "Sauk", 4): 75,
    ("1986-09-09", "governor", "Pierce", 4): 36,
    ("1986-09-09", "governor", "Jefferson", 4): 22,
    ("1986-09-09", "governor", "Fond du Lac", 4): 122,
    ("1986-09-09", "governor", "Door", 4): 18,
    ("1986-09-09", "governor", "Crawford", 4): 11,
    ("1986-09-09", "governor", "Clark", 4): 23,
    ("1986-09-09", "governor", "Calumet", 4): 29,
    ("1986-09-09", "governor", "Burnett", 4): 9,
    ("1986-09-09", "governor", "Buffalo", 4): 14,
    ("1986-09-09", "governor", "Brown", 4): 58,
    ("1986-09-09", "governor", "Ashland", 4): 14,
    ("1986-09-09", "governor", "Sauk", 3): 2135,
    ("1982-09-14", "senate", "Jackson", 5): 772,
    ("1982-09-14", "senate", "Ashland", 5): 488,
    ("1982-09-14", "senate", "Vilas", 4): 502,
    ("1982-09-14", "senate", "Ashland", 3): 7,
    ("1982-09-14", "senate", "Waukesha", 1): 2990,
    ("1982-09-14", "senate", "Washburn", 0): 4,
    ("1982-09-14", "senate", "Vilas", 0): 1,
    ("1982-09-14", "senate", "Ashland", 0): 1,
    ("1982-09-14", "attorney-general", "Marathon", 4): 0,
    ("1982-09-14", "attorney-general", "Burnett", 4): 0,
    ("1982-09-14", "attorney-general", "Waushara", 3): 0,
    ("1982-09-14", "attorney-general", "Sheboygan", 2): 61,
    ("1982-09-14", "attorney-general", "Marquette", 1): 752,
    ("1982-09-14", "attorney-general", "Sheboygan", 0): 22,
    ("1982-09-14", "attorney-general", "Shawano", 0): 46,
    ("1982-09-14", "attorney-general", "Marathon", 0): 71,
    ("1982-09-14", "attorney-general", "Kenosha", 0): 29,
    ("1982-09-14", "attorney-general", "Fond du Lac", 0): 33,
    ("1982-09-14", "attorney-general", "Eau Claire", 0): 7,
    ("1982-09-14", "attorney-general", "Dodge", 0): 35,
    ("1982-09-14", "attorney-general", "Dane", 0): 22,
    ("1982-09-14", "attorney-general", "Adams", 0): 3,
    ("1982-09-14", "treasurer", "Winnebago", 4): 5362,
    ("1982-09-14", "treasurer", "St. Croix", 4): 989,
    ("1982-09-14", "treasurer", "Pierce", 4): 512,
    ("1982-09-14", "treasurer", "Oconto", 4): 1633,
    ("1982-09-14", "treasurer", "Menominee", 4): 11,
    ("1982-09-14", "treasurer", "Jackson", 4): 597,
    ("1982-09-14", "treasurer", "Buffalo", 4): 909,
    ("1982-09-14", "treasurer", "Burnett", 3): 266,
    ("1982-09-14", "treasurer", "Wood", 3): 2803,
    ("1982-09-14", "treasurer", "Jackson", 3): 759,
    ("1982-09-14", "treasurer", "Rusk", 3): 513,
    ("1982-09-14", "treasurer", "Langlade", 3): 516,
    ("1982-09-14", "treasurer", "Buffalo", 3): 1231,
    ("1982-09-14", "treasurer", "Ashland", 3): 503,
    ("1982-09-14", "treasurer", "La Crosse", 1): 5026,
    ("1982-09-14", "treasurer", "Winnebago", 1): 7054,
    ("1982-09-14", "treasurer", "Polk", 1): 1830,
    ("1982-09-14", "treasurer", "Juneau", 1): 727,
    ("1982-09-14", "treasurer", "Marathon", 2): 24,
    ("1982-09-14", "treasurer", "La Crosse", 2): 22,
    ("1982-09-14", "treasurer", "Winnebago", 0): 38,
    ("1982-09-14", "treasurer", "Taylor", 0): 4,
    ("1982-09-14", "treasurer", "Sawyer", 0): 4,
    ("1982-09-14", "treasurer", "Oneida", 0): 5,
    ("1982-09-14", "treasurer", "Marquette", 0): 32,
    ("1982-09-14", "treasurer", "Langlade", 0): 5,
    ("1982-09-14", "treasurer", "Iowa", 0): 1,
    ("1982-09-14", "treasurer", "Florence", 0): 4,
    ("1982-09-14", "treasurer", "Adams", 0): 2,
    ("1982-09-14", "secretary-of-state", "Taylor", 6): 756,
    ("1982-09-14", "secretary-of-state", "La Crosse", 6): 5179,
    ("1982-09-14", "secretary-of-state", "Jefferson", 6): 4931,
    ("1982-09-14", "secretary-of-state", "Columbia", 6): 2951,
    ("1982-09-14", "secretary-of-state", "Washburn", 5): 5,
    ("1982-09-14", "secretary-of-state", "Trempealeau", 5): 5,
    ("1982-09-14", "secretary-of-state", "Rusk", 5): 5,
    ("1982-09-14", "secretary-of-state", "Green Lake", 5): 6,
    ("1982-09-14", "secretary-of-state", "Grant", 4): 194,
    ("1982-09-14", "secretary-of-state", "TOTAL", 4): 66576,
    ("1982-09-14", "secretary-of-state", "Sawyer", 4): 44,
    ("1982-09-14", "secretary-of-state", "Waushara", 4): 105,
    ("1982-09-14", "secretary-of-state", "Richland", 4): 74,
    ("1982-09-14", "secretary-of-state", "Portage", 4): 757,
    ("1982-09-14", "secretary-of-state", "Eau Claire", 4): 704,
    ("1982-09-14", "secretary-of-state", "Douglas", 4): 573,
    ("1982-09-14", "secretary-of-state", "Dodge", 4): 566,
    ("1982-09-14", "secretary-of-state", "Waushara", 3): 36,
    ("1982-09-14", "secretary-of-state", "Sheboygan", 3): 367,
    ("1982-09-14", "secretary-of-state", "Rock", 2): 4684,
    ("1982-09-14", "governor", "Marathon", 5): 1761,
    ("1982-09-14", "governor", "Monroe", 5): 1360,
    ("1982-09-14", "governor", "Langlade", 1): 724,
    ("1982-09-14", "governor", "Milwaukee", 1): 76374,
    ("1980-09-09", "senate", "Columbia", 4): 2065,
    ("1980-09-09", "senate", "Barron", 1): 956,
    ("1980-09-09", "senate", "Grant", 0): 0,
    ("1980-09-09", "senate", "Jefferson", 0): 15,
    ("1980-09-09", "senate", "Forest", 0): 0,
    ("1980-09-09", "senate", "Dunn", 0): 8,
    ("1980-09-09", "senate", "Chippewa", 0): 10,
    ("1982-11-02", "senate", "Trempealeau", 2): 10,
    ("1982-11-02", "senate", "Sauk", 2): 27,
    ("1992-11-03", "president", "Milwaukee", 13): 281,
    ("1988-11-08", "president", "Pierce", 5): 20,
    ("1988-11-08", "president", "Pepin", 5): 1,
    ("1986-11-04", "senate", "Rusk", 1): 2884,
    ("1986-11-04", "senate", "Brown", 0): 28572,
    ("1986-11-04", "senate", "Pierce", 0): 3663,
    ("1984-11-06", "president", "Dunn", 0): 49,
    ("1984-11-06", "president", "Wood", 0): 46,
    ("1984-11-06", "president", "Vilas", 0): 17,
    ("1984-11-06", "president", "Green", 0): 27,
    ("1982-11-02", "senate", "Shawano", 4): 24,
    ("1982-11-02", "senate", "Green", 4): 72,
    ("1982-11-02", "attorney-general", "Grant", 2): 247,
    ("1982-11-02", "attorney-general", "Grant", 1): 8123,
    ("1982-11-02", "attorney-general", "Grant", 0): 80,
    ("1982-11-02", "treasurer", "Vernon", 2): 101,
    ("1982-11-02", "treasurer", "Grant", 3): 6636,
    ("1982-11-02", "treasurer", "Grant", 2): 96,
    ("1982-11-02", "treasurer", "Grant", 1): 6137,
    ("1982-11-02", "treasurer", "Grant", 0): 31,
    ("1982-11-02", "secretary-of-state", "TOTAL", 0): 7559,
    ("1982-11-02", "secretary-of-state", "Washburn", 0): 22,
    ("1982-11-02", "secretary-of-state", "Richland", 0): 7,
    ("1982-11-02", "secretary-of-state", "St. Croix", 0): 29,
    ("1982-11-02", "secretary-of-state", "Rusk", 0): 49,
    ("1982-11-02", "secretary-of-state", "Door", 0): 14,
    ("1980-11-04", "senate", "Dodge", 4): 54,
    ("1980-11-04", "president", "Pepin", 9): 0,
    ("1980-11-04", "president", "Lafayette", 9): 1,
    ("1980-11-04", "president", "Crawford", 3): 3934,
    ("1980-11-04", "president", "Brown", 1): 29796,
    ("1978-11-07", "treasurer", "Door", 0): 201,
    ("1978-11-07", "treasurer", "Columbia", 2): 6671,
    ("1978-11-07", "treasurer", "La Crosse", 2): 12188,
    ("1978-11-07", "treasurer", "Polk", 2): 3579,
    ("1978-11-07", "treasurer", "Portage", 2): 7201,
}


def fetch() -> None:
    for edition, leaf in WIDTHS:
        pages = json.loads(raw.fetch(SOURCE, PAGES.format(BOOKS[edition]), suffix=".json").path.read_text())["pages"]
        page = next(index for index, entry in enumerate(pages) if entry["leafNum"] == leaf)
        raw.fetch(SOURCE, IMAGE.format(BOOKS[edition], page), suffix=".jpg")


def image(edition: str, leaf: int) -> str:
    pages = json.loads(raw.cached(SOURCE, PAGES.format(BOOKS[edition])).path.read_text())["pages"]
    return IMAGE.format(BOOKS[edition], next(index for index, entry in enumerate(pages) if entry["leafNum"] == leaf))


def grid(edition: str, leaf: int, order: list[str]) -> list[list[list[int]]]:
    width = WIDTHS[(edition, leaf)]
    source = ImageOps.autocontrast(Image.open(raw.cached(SOURCE, image(edition, leaf)).path).convert("L"), cutoff=2)
    half = source.resize((source.width // 2, source.height // 2), Image.Resampling.LANCZOS)
    third = source.resize((source.width // 3, source.height // 3), Image.Resampling.LANCZOS)
    double = source.resize((source.width * 2, source.height * 2), Image.Resampling.LANCZOS)
    variants = {
        "unsharp": (1.0, source.filter(ImageFilter.UnsharpMask(4, 200, 2))),
        "unsharp2": (1.0, source.filter(ImageFilter.UnsharpMask(2, 300, 0))),
        "halfunsharp": (0.5, half.filter(ImageFilter.UnsharpMask(2, 150, 2))),
        "halfsharp": (0.5, half.filter(ImageFilter.SHARPEN)),
        "x2unsharp": (2.0, double.filter(ImageFilter.UnsharpMask(6, 200, 2))),
        "third": (1 / 3, third.filter(ImageFilter.SHARPEN)),
        "local": (
            1.0,
            ImageChops.subtract(source.filter(ImageFilter.GaussianBlur(25)), source).point(
                lambda value: 255 - min(255, value * 5)
            ),
        ),
    }
    words: dict[str, list[tuple[float, float, float, str]]] = {}
    for mode, (scale, picture) in variants.items():
        cache = PARSED / "ocr" / f"{edition}-{leaf}-{mode}.tsv"
        if not cache.exists():
            buffer = io.BytesIO()
            picture.save(buffer, "PNG")
            cache.write_text(
                subprocess.run(
                    ["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
                    input=buffer.getvalue(),
                    capture_output=True,
                    check=True,
                ).stdout.decode()
            )
        words[mode] = [
            (
                (int(row["left"]) + int(row["width"])) / scale,
                (int(row["top"]) + int(row["height"]) / 2) / scale,
                int(row["left"]) / scale,
                row["text"].strip(),
            )
            for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE)
            if (row["text"] or "").strip()
        ]

    def clusters(values: list[float], gap: float) -> list[list[float]]:
        groups: list[list[float]] = []
        for value in sorted(values):
            if groups and value - groups[-1][-1] <= gap:
                groups[-1].append(value)
            else:
                groups.append([value])
        return groups

    numeric = {
        mode: [(right, middle, text) for right, middle, _, text in found if re.fullmatch(r"[\d,.]*\d[\d,.]*", text)]
        for mode, found in words.items()
    }
    columns = [
        (group[0] - 10, group[-1] + 10)
        for group in clusters([right for found in numeric.values() for right, _, _ in found], 18)
        if len(group) >= 100
    ]
    if len(columns) != width:
        raise ValueError(f"{edition} leaf {leaf}: {len(columns)} columns, expected {width}")
    for (index, (low, high)), (mode, picture) in itertools.product(
        enumerate(columns), (("strip", variants["unsharp"][1]), ("localstrip", variants["local"][1]))
    ):
        start = max(columns[index - 1][1] + 5 if index else low - 260, low - 260)
        cache = PARSED / "ocr" / f"{edition}-{leaf}-{mode}{index}.tsv"
        if not cache.exists():
            buffer = io.BytesIO()
            picture.crop((int(start), 0, int(high + 15), picture.height)).save(buffer, "PNG")
            cache.write_text(
                subprocess.run(
                    ["tesseract", "stdin", "stdout", "--psm", "6", "-c", "tessedit_char_whitelist=0123456789,", "tsv"],
                    input=buffer.getvalue(),
                    capture_output=True,
                    check=True,
                ).stdout.decode()
            )
        numeric[f"{mode}{index}"] = [
            (
                start + int(row["left"]) + int(row["width"]),
                int(row["top"]) + int(row["height"]) / 2,
                row["text"].strip(),
            )
            for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE)
            if re.fullmatch(r"[\d,]*\d[\d,]*", (row["text"] or "").strip())
        ]
    rows = [
        (group[0] - 6, group[-1] + 6)
        for group in clusters(
            [
                middle
                for found in numeric.values()
                for right, middle, _ in found
                if any(low <= right <= high for low, high in columns)
            ],
            8,
        )
        if len(group) >= width * 1.5
    ]
    while len(rows) > 1 and rows[1][0] - rows[0][1] > 150:
        rows.pop(0)
    pitch = statistics.median(b[0] - a[0] for a, b in itertools.pairwise(rows))
    if len(rows) == len(order):
        for index, (a, b) in enumerate(itertools.pairwise(rows[:-1])):
            if b[0] - a[0] > pitch * 1.7:
                rows.insert(index + 1, (a[0] + pitch, a[1] + pitch))
                break
    if len(rows) != len(order) + 1:
        raise ValueError(f"{edition} leaf {leaf}: {len(rows)} rows, expected {len(order) + 1}")
    labels = [
        re.sub(
            r"[^a-z]",
            "",
            " ".join(
                text
                for _, middle, left, text in words["unsharp"]
                if low <= middle <= high
                and (left < columns[0][0] - 150 or left > columns[-1][1] + 20)
                and not re.search(r"\d", text)
            ).lower(),
        )
        for low, high in rows
    ]
    names = [re.sub(r"[^a-z]", "", county.lower()) for county in order] + ["totals"]
    agree = sum(1 for label, name in zip(labels, names, strict=True) if name[:5] in label or name[1:6] in label)
    clash = [
        (label, name)
        for label, name in zip(labels, names, strict=True)
        if name[:5] not in label
        and name[1:6] not in label
        and (close := difflib.get_close_matches(label, names, 1, 0.85))
        and close[0] != name
    ]
    if agree < 8 or clash:
        raise ValueError(f"{edition} leaf {leaf}: county order does not line up ({agree} agree, {clash})")
    cells: list[list[list[int]]] = [[[] for _ in columns] for _ in rows]
    for found in numeric.values():
        for right, middle, text in found:
            column = next((index for index, (low, high) in enumerate(columns) if low <= right <= high), None)
            row = next((index for index, (low, high) in enumerate(rows) if low <= middle <= high), None)
            if column is not None and row is not None:
                cells[row][column].append(int(re.sub(r"\D", "", text)))
    return cells


def parse() -> None:
    (PARSED / "ocr").mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        counties = dict(
            conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '55%%'"""
            ).fetchall()
        )
    if len(counties) != 72:
        raise ValueError("Wisconsin needs 72 counties")
    order = sorted(counties, key=lambda county: re.sub(r"^St\. ", "Saint ", county).lower())
    grids: dict[tuple[str, int], list[list[list[int]]]] = {}
    rows: list[dict[str, object]] = []
    caveats: list[dict[str, object]] = [{"day": day, "office": office, "note": note} for day, office, note in NOTES]
    for day, office, pages, candidates in TABLES:
        readings: list[list[list[int]]] = [[] for _ in range(len(order) + 1)]
        for edition, leaf, first in pages:
            if (edition, leaf) not in grids:
                grids[(edition, leaf)] = grid(edition, leaf, order)
            for row, cells in enumerate(grids[(edition, leaf)]):
                readings[row].extend(cells[first:])
        readings = [row[: len(candidates)] for row in readings]
        chosen: list[list[int | None]] = []
        for row in readings:
            chosen.append([])
            for cell in row:
                counts = Counter(cell).most_common()
                ranked = [value for value, _ in counts]
                longer = [
                    value for value, count in counts if str(value)[1:] == str(ranked[0]) and count * 3 >= counts[0][1]
                ]
                chosen[-1].append((longer or ranked or [None])[0])
        for (when, what, county, column), votes in FIXES.items():
            if (when, what) == (day, office):
                chosen[(order + ["TOTAL"]).index(county)][column] = votes
        for column, (_, label) in enumerate(candidates):
            blank = [order[row] for row in range(len(order)) if chosen[row][column] is None]
            if blank or chosen[-1][column] is None:
                raise ValueError(f"{day} {office} {label}: no reading for {blank or 'TOTAL'}")
            printed = {chosen[-1][column], *readings[-1][column]}
            total = sum(chosen[row][column] or 0 for row in range(len(order)))
            if total in printed:
                continue
            gap = (chosen[-1][column] or 0) - total
            others = sorted(
                {
                    (row, value)
                    for row in range(len(order))
                    for value in readings[row][column]
                    if value != chosen[row][column]
                }
            )
            swaps = [
                chosen_set
                for size in (1, 2)
                for chosen_set in itertools.combinations(others, size)
                if len({row for row, _ in chosen_set}) == size
                and sum(value - (chosen[row][column] or 0) for row, value in chosen_set) == gap
            ]
            if (day, office, column) in CHECKED:
                caveats.append(
                    {
                        "day": day,
                        "office": office,
                        "note": f"The Blue Book's printed total for {label} is {chosen[-1][column]:,}; "
                        f"its county figures sum to {total:,}.",
                    }
                )
                continue
            if not swaps or len([one for one in swaps if len(one) == len(swaps[0])]) > 1:
                raise ValueError(
                    f"{day} {office} {label}: off by {gap}, other readings "
                    f"{[(order[row], value) for row, value in others]}"
                )
            for row, value in swaps[0]:
                chosen[row][column] = value
        for row, county in enumerate(order):
            for (party, label), votes in zip(candidates, chosen[row], strict=True):
                rows.append(
                    {
                        "day": day,
                        "office": office,
                        "county": counties[county],
                        "label": label,
                        "party": party,
                        "votes": votes,
                    }
                )
    pl.DataFrame(rows).write_parquet(PARSED / "lines.parquet")
    pl.DataFrame(caveats, schema={"day": pl.String, "office": pl.String, "note": pl.String}).write_parquet(
        PARSED / "caveats.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    editions = {(day, office): pages[0][0] for day, office, pages, _ in TABLES}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wisconsin's president, governor, U.S. Senate, and statewide offices by county 1978–1992, "
            "with the 1980–1992 primaries, from the Wisconsin Blue Book",
            polity="US",
        )

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def parts(text: str) -> tuple[list[str], set[str]]:
            words = [plain(word) for word in re.sub(r'"[^"]*"|\([^)]*\)', " ", text).split()]
            suffixes = {"jr", "sr", "ii", "iii", "iv"}
            return [word for word in words if word and word not in suffixes], set(words) & suffixes

        def surname(text: str) -> str:
            words = parts(text)[0]
            return words[-1] if words else ""

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources = {edition: cs.source(PUBLISHER, f"Wisconsin Blue Book {edition}") for edition in BOOKS}
        for edition, item in BOOKS.items():
            cs.add(
                "source",
                id=sources[edition],
                kind="official_body",
                publisher=PUBLISHER,
                title=f"Wisconsin Blue Book {edition}",
                url=f"https://archive.org/details/{item}",
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, PAGES.format(item)), sources[edition])
        for edition, leaf in sorted({(edition, leaf) for _, _, pages, _ in TABLES for edition, leaf, _ in pages}):
            cs.artifact(raw.cached(SOURCE, image(edition, leaf)), sources[edition])
        caveats: dict[tuple[str, str], list[str]] = {}
        for day, office, note in pl.read_parquet(PARSED / "caveats.parquet").iter_rows():
            caveats.setdefault((day, office), []).append(note)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '55%%'"
            ).fetchall()
        )
        if len(counties) != 72:
            raise ValueError("Wisconsin needs 72 counties")
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, list[int]]] = {}
        texts: dict[UUID, str] = {}
        for person, text, statewide, known, years in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':WI:[a-z-]+(:special|:democratic|:republican)?$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}.*(:WI:|:president$)' and x.value !~ ':WI:55\\d{3}:'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if not text.split():
                continue
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            ranks[person] = (statewide, known, years)
            texts[person] = text

        def fits(one: UUID, candidate: str, year: int) -> bool:
            if min((abs(other - year) for other in ranks[one][2]), default=0) > 30:
                return False
            mine, mark = parts(candidate)
            theirs, their_mark = parts(texts[one])
            if mark & {"ii", "iii", "iv"} != their_mark & {"ii", "iii", "iv"}:
                return False
            return len(mine) < 3 or len(theirs) < 3 or mine[1][:1] == theirs[1][:1]

        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", party) if party else None for code, party in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()} - {None}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':WI:'")
        )
        ((area,),) = conn.execute(
            "select district_area_id from contest where id = %s", [contests["1978-11-07:WI:governor"]]
        ).fetchall()
        elections: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.election' and value ~ '^\\d{4}-\\d\\d-\\d\\d:WI$'"
            )
        )
        born: set[UUID] = set()
        primaries = {"1980-09-09", "1982-09-14", "1986-09-09", "1988-09-13", "1990-09-11", "1992-09-08"}
        kinds = {
            "governor": "gubernatorial",
            "secretary-of-state": "Secretary of State",
            "treasurer": "State Treasurer",
            "attorney-general": "Attorney General",
        }
        scopes = {code: re.sub(r"^WI:|-party$", "", party) for code, party in PARTIES.items() if party}
        frame = frame.with_columns(
            pl.when(pl.col("day").is_in(sorted(primaries)))
            .then(pl.col("party").replace_strict(scopes, default=""))
            .otherwise(pl.lit(""))
            .alias("scope")
        )
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
        for (day, office, scope), lines in frame.group_by("day", "office", "scope", maintain_order=True):
            key = f"{day}:WI:{office}:{scope}" if scope else f"{day}:WI:{office}"
            year = int(day[:4])
            result_set = cs.key(f"{SOURCE}.result_set", key)
            contest = contests.get(key)
            if scope:
                election_id = cs.key("us.election", f"{day}:WI:primary")
                if election_id in cs.minted and election_id not in born:
                    born.add(election_id)
                    cs.add(
                        "election",
                        id=election_id,
                        polity_id=cs.polity("US-WI"),
                        kind="primary",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    name(election_id, f"{year} Wisconsin primary election")
                    cs.add("slug", path=f"us/wi/{day}-primary", entity_id=election_id)
                contest = cs.key("us.contest", key)
                party = scope.replace("-", " ").title().replace("Labor Farm", "Labor-Farm")
                if office == "senate":
                    ((body, seat, district, term),) = conn.execute(
                        "select body_id, seat_id, district_area_id, term from contest where id = %s",
                        [next(v for k, v in contests.items() if re.fullmatch(rf"{year}-11-\d\d:WI:senate", k))],
                    ).fetchall()
                    shape = {"kind": "body_seats", "body_id": body, "seat_id": seat}
                    title = f"{year} U.S. Senate {party} primary in Wisconsin"
                else:
                    office_id = cs.key("us.office", f"{office}:WI")
                    if office_id in cs.minted:
                        raise ValueError(f"no office {office}")
                    district, term = area, "full"
                    shape = {"kind": "office", "office_id": office_id}
                    title = f"{year} Wisconsin {kinds[office]} {party} primary"
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    **shape,
                    district_area_id=district,
                    term=term,
                    magnitude=1,
                    party_scope_id=parties[lines["party"][0]],
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, title)
                cs.add("slug", path=f"us/wi/{day}-primary/{office}-{scope}", entity_id=contest)
            elif contest is None:
                office_id = cs.key("us.office", f"{office}:WI")
                if office_id in cs.minted:
                    raise ValueError(f"no office {office}")
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[f"{day}:WI"],
                    kind="office",
                    office_id=office_id,
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {TITLES[office]}")
                cs.add("slug", path=f"us/wi/{day}-general/{office}", entity_id=contest)
                election_id = elections[f"{day}:WI"]
            else:
                ((election_id,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket')""",
                [contest],
            ).fetchall()
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
            for label, code, votes in (
                lines.group_by("label", "party", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                matches = [option for option, _, text in options if surname(text or "") == surname(label)]
                if len(matches) > 1:
                    matches = [option for option in matches if option in on_file] or matches
                if len(matches) > 1:
                    texts_by = {text: option for option, _, text in options if option in matches}
                    matches = [texts_by[difflib.get_close_matches(label, list(texts_by), 1, 0)[0]]]
                if matches:
                    option = matches[0]
                else:
                    first = plain(label.split()[0])
                    known = {
                        one
                        for one in people.get(plain(label)) or people.get(f"{first}|{surname(label)}", set())
                        if fits(one, label, year)
                    }
                    person = (
                        max(
                            known,
                            key=lambda one: (
                                ranks[one][0],
                                ranks[one][1],
                                -min(abs(other - year) for other in ranks[one][2]),
                            ),
                        )
                        if known
                        else cs.key(f"{SOURCE}.person", f"WI:{slug(label)}")
                    )
                    if person in cs.minted and person not in born:
                        born.add(person)
                        cs.add("person", id=person)
                        name(person, label)
                    elif person not in cs.minted:
                        print(f"{key}: {label} is joined to {texts[person]}")
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket" if office == "president" else "candidate",
                        party_id=parties[code],
                        ballot_label=label,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                    )
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {label} has {votes}, {on_file[option]} on file")
                totals[option] = totals.get(option, 0) + votes
                picked[label] = option
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[editions[(day, office)]],
                is_default=True,
                completeness=1,
            )
            notes = [
                note for note in caveats.get((day, office), []) if not scope or any(label in note for label in picked)
            ]
            if notes:
                prior = conn.execute(
                    "select id from note where entity_id = %s and kind = 'caveat'", [result_set]
                ).fetchall()
                cs.add(
                    "note",
                    **({"id": prior[0][0]} if len(prior) == 1 else {}),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=" ".join(notes),
                )
            if conn.execute("select from result_set where id = %s", [result_set]).fetchone() is None:
                for county, label, votes in lines.select("county", "label", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[county],
                        option_id=picked[label],
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
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=winner,
                    kind="won" if scope else "elected",
                    seats=None if scope else 1,
                )
        return cs.write()
