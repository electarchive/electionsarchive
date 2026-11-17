"""The tables of each historical Folketing volume: pages, columns, parties, and national seats."""

from dataclasses import replace
from datetime import date

from ea.sources.dk.dst_folketing import SYSTEM_1953, SYSTEM_1953_APRIL, Volume, part

ACCOUNTING_1971 = ("registered", "pct", "cast", "invalid", "blank", "other")
ACCOUNTING_1973 = ("registered", "pct", "cast", "invalid", "blank", "other", "valid")
# From 1977 the volumes add postal ballots set aside and admitted, which are part of the ballots cast.
ACCOUNTING_1977 = ("registered", "cast", "skip", "skip", "invalid", "blank", "other", "pct", "pct", "pct", "valid")
NAMES = {
    "A": "Socialdemokratiet",
    "B": "Det Radikale Venstre",
    "C": "Det Konservative Folkeparti",
    "D": "Centrum-Demokraterne",
    "E": "Retsforbundet",
    "F": "Socialistisk Folkeparti",
    "K": "Danmarks Kommunistiske Parti",
    "M": "Centrum-Demokraterne",
    "Q": "Kristeligt Folkeparti",
    "S": "Slesvigsk Parti",
    "V": "Venstre",
    "Y": "Venstresocialisterne",
    "Z": "Fremskridtspartiet",
}


def names(*letters: str, **extra: str) -> dict[str, str]:
    return {letter: extra.get(letter) or NAMES[letter] for letter in letters}


def spreads(pages: list[int], left: tuple[str, ...], right: tuple[str, ...], edge: float) -> tuple:
    return tuple((part(page, *left, left=edge), part(page + 1, *right)) for page in pages)


VOLUMES = [
    Volume(
        date(1971, 9, 21),
        20207,
        "valg1971",
        "Folketingsvalget den 21. september 1971",
        (
            (
                part(58, *ACCOUNTING_1971, left=180),
                part(59, "valid", "A", "B", "C", "E", "F", "K", "Q", "S", "V", "Y", "others"),
            ),
            (
                part(60, *ACCOUNTING_1971, left=180),
                part(61, "valid", "A", "B", "C", "E", "F", "K", "Q", "S", "V", "Y", "others"),
            ),
            (
                part(62, *ACCOUNTING_1971, left=180),
                part(63, "valid", "A", "B", "C", "E", "F", "K", "Q", "S", "V", "Y", "others"),
            ),
        ),
        {
            "A": "Socialdemokratiet",
            "B": "Det Radikale Venstre",
            "C": "Det Konservative Folkeparti",
            "E": "Retsforbundet",
            "F": "Socialistisk Folkeparti",
            "K": "Danmarks Kommunistiske Parti",
            "Q": "Kristeligt Folkeparti",
            "S": "Slesvigsk Parti",
            "V": "Venstre",
            "Y": "Venstresocialisterne",
        },
        {"A": 70, "B": 27, "C": 31, "F": 17, "V": 30},
        76,
    ),
    Volume(
        date(1973, 12, 4),
        20206,
        "valg1973",
        "Folketingsvalget den 4. december 1973",
        spreads([68, 70, 72], ACCOUNTING_1973, ("A", "B", "C", "E", "F", "K", "M", "Q", "V", "Y", "Z", "others"), 160),
        names("A", "B", "C", "E", "F", "K", "M", "Q", "V", "Y", "Z"),
        {"A": 46, "B": 20, "C": 16, "E": 5, "F": 11, "K": 6, "M": 14, "Q": 7, "V": 22, "Z": 28},
        88,
    ),
    Volume(
        date(1975, 1, 9),
        20205,
        "valg1975",
        "Folketingsvalget den 9. januar 1975",
        spreads([68, 70, 72], ACCOUNTING_1973, ("A", "B", "C", "E", "F", "K", "M", "Q", "V", "Y", "Z", "others"), 160),
        names("A", "B", "C", "E", "F", "K", "M", "Q", "V", "Y", "Z"),
        {"A": 53, "B": 13, "C": 10, "F": 9, "K": 7, "M": 4, "Q": 9, "V": 42, "Y": 4, "Z": 24},
        88,
    ),
    Volume(
        date(1977, 2, 15),
        20204,
        "valg1977",
        "Folketingsvalget den 15. februar 1977",
        spreads(
            [68, 70, 72], ACCOUNTING_1977, ("A", "B", "C", "E", "F", "K", "M", "P", "Q", "V", "Y", "Z", "others"), 135
        ),
        names("A", "B", "C", "E", "F", "K", "M", "P", "Q", "V", "Y", "Z", P="Pensionistpartiet"),
        {"A": 65, "B": 6, "C": 15, "E": 6, "F": 7, "K": 7, "M": 11, "Q": 6, "V": 21, "Y": 5, "Z": 26},
        88,
    ),
    Volume(
        date(1979, 10, 23),
        20203,
        "valg1973",
        "Folketingsvalget den 23. oktober 1979",
        spreads(
            [68, 70, 72], ACCOUNTING_1977, ("A", "B", "C", "E", "F", "K", "M", "Q", "R", "V", "Y", "Z", "others"), 135
        ),
        names("A", "B", "C", "E", "F", "K", "M", "Q", "R", "V", "Y", "Z", R="Kommunistisk Arbejderparti"),
        {"A": 68, "B": 10, "C": 22, "E": 5, "F": 11, "M": 6, "Q": 5, "V": 22, "Y": 6, "Z": 20},
        88,
    ),
    Volume(
        date(1981, 12, 8),
        20202,
        "valg1981",
        "Folketingsvalget den 8. december 1981",
        spreads(
            [70, 72, 74],
            ACCOUNTING_1977,
            ("A", "B", "C", "E", "F", "I", "K", "M", "Q", "R", "V", "Y", "Z", "others"),
            135,
        ),
        names(
            "A",
            "B",
            "C",
            "E",
            "F",
            "I",
            "K",
            "M",
            "Q",
            "R",
            "V",
            "Y",
            "Z",
            I="Socialistisk Arbejderparti",
            R="Kommunistisk Arbejderparti",
        ),
        {"A": 59, "B": 9, "C": 26, "F": 21, "M": 15, "Q": 4, "V": 20, "Y": 5, "Z": 16},
        90,
    ),
    Volume(
        date(1984, 1, 10),
        20201,
        "valg1984",
        "Folketingsvalget den 10. januar 1984",
        spreads(
            [70, 72, 74],
            ACCOUNTING_1977,
            ("A", "B", "C", "E", "F", "I", "K", "L", "M", "Q", "V", "Y", "Z", "others"),
            135,
        ),
        names(
            "A",
            "B",
            "C",
            "E",
            "F",
            "I",
            "K",
            "L",
            "M",
            "Q",
            "V",
            "Y",
            "Z",
            I="Socialistisk Arbejderparti",
            L="Marxistisk-Leninistisk Parti",
        ),
        {"A": 56, "B": 10, "C": 42, "F": 21, "M": 8, "Q": 5, "V": 22, "Y": 5, "Z": 6},
        90,
    ),
    Volume(
        date(1987, 9, 8),
        20200,
        "valg1987",
        "Folketingsvalget den 8. september 1987",
        spreads(
            [58, 60, 62],
            ACCOUNTING_1977,
            ("A", "B", "C", "E", "F", "G", "H", "I", "K", "L", "M", "P", "Q", "V", "Y", "Z", "others"),
            135,
        ),
        names(
            "A",
            "B",
            "C",
            "E",
            "F",
            "G",
            "H",
            "I",
            "K",
            "L",
            "M",
            "P",
            "Q",
            "V",
            "Y",
            "Z",
            G="De Grønne",
            H="Humanistisk Parti",
            I="Socialistisk Arbejderparti",
            L="Marxistisk-Leninistisk Parti",
            P="Fælles Kurs",
        ),
        {"A": 54, "B": 11, "C": 38, "F": 27, "M": 9, "P": 4, "Q": 4, "V": 19, "Z": 9},
        76,
    ),
    Volume(
        date(1988, 5, 10),
        20199,
        "valgbd2",
        "Folketingsvalget den 10. maj 1988, Bind II",
        (
            *spreads(
                [42, 44], ACCOUNTING_1977, ("A", "B", "C", "F", "G", "K", "M", "P", "Q", "V", "Y", "Z", "others"), 160
            ),
            (
                part(46, *ACCOUNTING_1977, left=160, bottom=300),
                part(47, "A", "B", "C", "F", "G", "K", "M", "P", "Q", "V", "Y", "Z", "others", bottom=300),
            ),
        ),
        names("A", "B", "C", "F", "G", "K", "M", "P", "Q", "V", "Y", "Z", G="De Grønne", P="Fælles Kurs"),
        {"A": 55, "B": 10, "C": 35, "F": 24, "M": 9, "Q": 4, "V": 22, "Z": 16},
        56,
    ),
]


def tables(accounting: tuple[str, ...], parties: tuple[str, ...], left: tuple[float, float]) -> tuple:
    """Tabel 4 (pages 8 to 10) and Tabel 5 (pages 11 to 13) of the volumes from 1994: the same rows."""
    return (
        (
            tuple(part(page, *accounting, left=left[0]) for page in (8, 9, 10)),
            tuple(part(page, *parties, left=left[1]) for page in (11, 12, 13)),
        ),
    )


ACCOUNTING_1994 = ("registered", "cast", "skip", "invalid", "blank", "other", "pct", "pct", "pct", "valid4")
ACCOUNTING_1998 = ("registered", "cast", "skip", "skip", "invalid", "blank", "other", "pct", "pct", "pct", "valid4")
ACCOUNTING_2005 = ("skip", *ACCOUNTING_1998)
NAMES_1990 = {
    **NAMES,
    "D": "Centrum-Demokraterne",
    "G": "De Grønne",
    "H": "Humanistisk Parti",
    "O": "Dansk Folkeparti",
    "P": "Fælles Kurs",
    "U": "Demokratisk Fornyelse",
    "Ø": "Enhedslisten - De Rød-Grønne",
}
NAMES_2005 = {**NAMES_1990, "K": "Kristendemokraterne", "M": "Minoritetspartiet"}


def later(*letters: str, base: dict[str, str] = NAMES_1990) -> dict[str, str]:
    return {letter: base[letter] for letter in letters}


VOLUMES += [
    Volume(
        date(1990, 12, 12),
        20327,
        "valg1990",
        "Folketingsvalget den 12. december 1990",
        tuple(
            (
                part(page, *ACCOUNTING_1977, left=160, bottom=340 if page == 12 else 1e9),
                part(
                    page + 1,
                    *("A", "B", "C", "D", "E", "F", "G", "H", "P", "Q", "V", "Z", "Ø", "others"),
                    top=160,
                    bottom=340 if page == 12 else 1e9,
                ),
            )
            for page in (8, 10, 12)
        ),
        later("A", "B", "C", "D", "E", "F", "G", "H", "P", "Q", "V", "Z", "Ø"),
        {"A": 69, "B": 7, "C": 30, "D": 9, "F": 15, "Q": 4, "V": 29, "Z": 12},
        22,
    ),
    Volume(
        date(1994, 9, 21),
        20328,
        "valg1994",
        "Folketingsvalget den 21. september 1994",
        tables(ACCOUNTING_1994, ("A", "B", "C", "D", "F", "Q", "V", "Z", "Ø", "others", "valid"), (140, 160)),
        later("A", "B", "C", "D", "F", "Q", "V", "Z", "Ø"),
        {"A": 62, "B": 8, "C": 27, "D": 5, "F": 13, "V": 42, "Z": 11, "Ø": 6, "others": 1},
        22,
    ),
    Volume(
        date(1998, 3, 11),
        20329,
        "valg1998",
        "Folketingsvalget den 11. marts 1998",
        tables(ACCOUNTING_1998, ("A", "B", "C", "D", "F", "O", "Q", "U", "V", "Z", "Ø", "others", "valid"), (140, 150)),
        later("A", "B", "C", "D", "F", "O", "Q", "U", "V", "Z", "Ø"),
        {"A": 63, "B": 7, "C": 16, "D": 8, "F": 13, "O": 13, "Q": 4, "V": 42, "Z": 4, "Ø": 5},
        23,
    ),
    Volume(
        date(2001, 11, 20),
        20330,
        "valg2001",
        "Folketingsvalget den 20. november 2001",
        tables(ACCOUNTING_1998, ("A", "B", "C", "D", "F", "O", "Q", "V", "Z", "Ø", "others", "valid"), (150, 145)),
        later("A", "B", "C", "D", "F", "O", "Q", "V", "Z", "Ø"),
        {"A": 52, "B": 9, "C": 16, "F": 12, "O": 22, "Q": 4, "V": 56, "Ø": 4},
        22,
    ),
    Volume(
        date(2005, 2, 8),
        20331,
        "valg2005",
        "Folketingsvalget den 8. februar 2005 - Revideret opgørelse",
        tables(ACCOUNTING_2005, ("A", "B", "C", "D", "F", "K", "M", "O", "V", "Ø", "others", "valid"), (140, 140)),
        later("A", "B", "C", "D", "F", "K", "M", "O", "V", "Ø", base=NAMES_2005),
        {"A": 47, "B": 17, "C": 18, "F": 11, "O": 24, "V": 52, "Ø": 6},
        22,
    ),
]


AUTO = -1.0  # each line's label ends with its last word, since the scan sets rows off sideways
EARLY = {
    **NAMES,
    "D": "Venstre",
    "L": "Liberalt Centrum",
    "M": "Fredspolitisk Folkeparti",
    "R": "Dansk Samling",
    "U": "De Uafhængige",
}


def early(*letters: str) -> dict[str, str]:
    return {letter: EARLY[letter] for letter in letters}


def pages(roles: tuple[str, ...], edges: dict[int, tuple[float, ...]]) -> tuple:
    """Single-page tables whose columns are given by their right edges, read from the page's text layer."""
    return tuple((part(page, *roles, left=AUTO, edges=columns),) for page, columns in edges.items())


ROLES_1960 = ("A", "B", "C", "D", "E", "F", "K", "S", "U", "others", "valid")
VOLUMES[:0] = [
    Volume(
        date(1953, 4, 21),
        20213,
        "rigsvalg",
        "Rigsdagsvalgene og folkeafstemningerne i april og maj 1953",
        pages(
            ("A", "B", "C", "D", "E", "K", "R", "others", "valid"),
            {
                79: (151, 176, 206, 232, 260, 287, 311, 330, 365),
                80: (173, 203, 231, 259, 287, 315, 338, 358, 396),
                81: (152, 179, 206, 234, 262, 290, 314, 333, 372),
                82: (163, 191, 219, 247, 273, 300, 328, 350, 388),
                83: (145, 170, 199, 227, 253, 281, 309, 331, 369),
                84: (167, 194, 222, 250, 278, 306, 334, 353, 392),
            },
        ),
        early("A", "B", "C", "D", "E", "K", "R"),
        {"A": 61, "B": 13, "C": 26, "D": 33, "E": 9, "K": 7},
        85,
        magnitude=149,
        system=SYSTEM_1953_APRIL,
        after=True,
        dots=True,
        adaptive=True,
    ),
    Volume(
        date(1953, 9, 22),
        20213,
        "valg1953",
        "Folketingsvalget den 22. september 1953",
        pages(
            ("A", "B", "C", "D", "E", "K", "U", "others", "valid"),
            {
                92: (170, 196, 224, 251, 279, 307, 330, 347, 384),
                93: (145, 172, 199, 226, 254, 282, 306, 323, 364),
                94: (166, 193, 220, 248, 276, 304, 327, 344, 385),
                95: (153, 179, 206, 234, 262, 290, 313, 330, 371),
            },
        ),
        early("A", "B", "C", "D", "E", "K", "U"),
        {"A": 74, "B": 14, "C": 30, "D": 42, "E": 6, "K": 8, "others": 1},
        96,
        notes=(
            (
                "The volume counts the Schleswig Party's list (Slesvigsk Parti), which stood in Haderslev m.fl. Amters "
                "Amtskreds only, among the votes outside the parties; its one seat is stored on that option."
            ),
        ),
        system=SYSTEM_1953,
        after=True,
        dots=True,
        adaptive=True,
    ),
    Volume(
        date(1957, 5, 14),
        20212,
        "valg1957",
        "Folketingsvalget den 14. maj 1957",
        pages(
            ("A", "B", "C", "D", "E", "K", "S", "U", "others", "valid"),
            {
                90: (156, 184, 212, 240, 268, 291, 313, 334, 359, 388),
                91: (138, 166, 194, 221, 250, 273, 296, 316, 340, 365),
                92: (154, 182, 210, 237, 265, 289, 311, 332, 350, 386),
                93: (136, 164, 192, 220, 248, 271, 293, 314, 340, 367),
            },
        ),
        early("A", "B", "C", "D", "E", "K", "S", "U"),
        {"A": 70, "B": 14, "C": 30, "D": 45, "E": 9, "K": 6, "S": 1},
        94,
        # Page 90 prints a dot, for no votes, in the outside-the-parties column of Falkonér and of the Vestre
        # Storkreds total; the text layer reads each as a one.
        fixes={(23, "others"): 0, (24, "others"): 0},
        system=SYSTEM_1953,
        after=True,
        dots=True,
        adaptive=True,
    ),
    Volume(
        date(1960, 11, 15),
        20211,
        "valg1960",
        "Folketingsvalget den 15. november 1960",
        (
            (
                part(
                    24,
                    *ROLES_1960,
                    left=AUTO,
                    bottom=400,
                    edges=(188, 215, 247, 274, 296, 323, 350, 367, 399, 417, 454),
                ),
            ),
            (part(24, *ROLES_1960, left=AUTO, top=400, edges=(157, 188, 220, 251, 278, 310, 337, 358, 391, 413, 454)),),
            *pages(
                ROLES_1960,
                {
                    25: (151, 183, 214, 246, 273, 304, 331, 353, 385, 407, 444),
                    26: (156, 187, 219, 250, 277, 309, 336, 358, 390, 412, 453),
                    27: (149, 181, 212, 243, 271, 302, 329, 350, 383, 405, 446),
                },
            ),
        ),
        early("A", "B", "C", "D", "E", "F", "K", "S", "U"),
        {"A": 76, "B": 11, "C": 32, "D": 38, "F": 11, "S": 1, "U": 6},
        28,
        system=SYSTEM_1953,
        after=True,
        dots=True,
        adaptive=True,
    ),
    Volume(
        date(1964, 9, 22),
        20210,
        "valg1964",
        "Folketingsvalget den 22. september 1964",
        tuple(
            (
                part(page, "A", "B", "C", "D", left=AUTO),
                part(page + 1, "E", "F", "K", "M", "R", "S", "U", "others", "valid"),
            )
            for page in (32, 34, 36, 38)
        ),
        early("A", "B", "C", "D", "E", "F", "K", "M", "R", "S", "U"),
        {"A": 76, "B": 10, "C": 36, "D": 38, "F": 10, "U": 5},
        52,
        after=True,
    ),
    Volume(
        date(1966, 11, 22),
        20209,
        "valg1966",
        "Folketingsvalget den 22. november 1966",
        tuple(
            (
                part(page, "valid", "A", "B", "C", left=AUTO, bottom=300 if page == 44 else 1e9),
                part(page + 1, "D", "E", "F", "K", "L", "U", "others", bottom=300 if page == 44 else 1e9),
            )
            for page in (38, 40, 42, 44)
        ),
        early("A", "B", "C", "D", "E", "F", "K", "L", "U"),
        {"A": 69, "B": 13, "C": 34, "D": 35, "F": 20, "L": 4},
        58,
    ),
    Volume(
        date(1968, 1, 23),
        20208,
        "valg1968",
        "Folketingsvalget den 23. januar 1968",
        tuple(
            (
                part(page, "registered", "pct", "cast", "blank", "other", "valid", "A", left=AUTO, bottom=bottom),
                part(page + 1, "B", "C", "D", "E", "F", "K", "L", "S", "U", "Y", "others", bottom=bottom),
            )
            for page, bottom in ((46, 1e9), (48, 1e9), (50, 1e9), (52, 300.0))
        ),
        early("A", "B", "C", "D", "E", "F", "K", "L", "S", "U", "Y"),
        {"A": 62, "B": 27, "C": 37, "D": 34, "F": 11, "Y": 4},
        64,
    ),
]

# The page of each volume's table of all seats ("Samlet fordeling af folketingsmandaterne"), where the national seats
# are printed.
SEAT_PAGES = {
    date(1953, 4, 21): 96,
    date(1953, 9, 22): 106,
    date(1957, 5, 14): 104,
    date(1960, 11, 15): 36,
    date(1964, 9, 22): 52,
    date(1966, 11, 22): 58,
    date(1968, 1, 23): 66,
    date(1971, 9, 21): 76,
    date(1973, 12, 4): 90,
    date(1975, 1, 9): 90,
    date(1977, 2, 15): 90,
    date(1979, 10, 23): 90,
    date(1981, 12, 8): 90,
    date(1984, 1, 10): 90,
    date(1987, 9, 8): 78,
    date(1988, 5, 10): 56,
    date(1990, 12, 12): 22,
    date(1994, 9, 21): 22,
    date(1998, 3, 11): 23,
    date(2001, 11, 20): 22,
    date(2005, 2, 8): 22,
}
VOLUMES = [replace(volume, seats_page=SEAT_PAGES[volume.day]) for volume in VOLUMES]
