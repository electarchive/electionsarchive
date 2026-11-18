"""Seats won by each list in each constituency of the Romanian parliamentary election of 1 December 2024.

The Central Electoral Bureau declared the final results in two records: the one for the Senate (RF 2) lists the
senators elected, and the one for the Chamber of Deputies (RF 3) lists the deputies elected, each with the list that
elected them and their constituency. The Bureau publishes the records as scans, so the seats were counted from the
lists of elected members (section k of each record), and every constituency's total was checked against the seats the
record allocates to it in its first and second stages (section i.2 and j). The 19 seats of the national minority
organizations are filled outside the constituencies and are not part of these tables.

Each entry is a constituency number, as the results portal numbers them (1 to 41 for the counties, 42 for Bucharest,
43 for Romanians abroad), and the lists that won a seat there, one name per seat."""

SENATE_URL = "https://parlamentare2024.bec.ro/wp-content/uploads/2024/12/RF_2.pdf"
CHAMBER_URL = "https://parlamentare2024.bec.ro/wp-content/uploads/2024/12/RF_3.pdf"
URLS = (SENATE_URL, CHAMBER_URL)

LISTS = {
    "AUR": "ALIANȚA PENTRU UNIREA ROMÂNILOR",
    "PSD": "PARTIDUL SOCIAL DEMOCRAT",
    "PNL": "PARTIDUL NAȚIONAL LIBERAL",
    "USR": "UNIUNEA SALVAȚI ROMÂNIA",
    "SOS": "PARTIDUL S.O.S. ROMÂNIA",
    "POT": "PARTIDUL OAMENILOR TINERI",
    "UDMR": "UNIUNEA DEMOCRATĂ MAGHIARĂ DIN ROMÂNIA",
}

SENATE = {
    1: "PNL AUR",
    2: "PNL USR AUR",
    3: "PSD PNL USR AUR",
    4: "PNL USR AUR PSD",
    5: "UDMR PNL PNL PSD",
    6: "PSD POT",
    7: "PNL AUR PSD",
    8: "USR PSD AUR PNL",
    9: "PSD SOS",
    10: "AUR PSD PSD",
    11: "PSD POT",
    12: "PSD SOS",
    13: "USR PNL AUR UDMR",
    14: "USR PSD PNL AUR SOS",
    15: "UDMR UDMR",
    16: "PSD USR AUR",
    17: "AUR PSD PSD PNL",
    18: "USR PSD AUR SOS",
    19: "PNL",
    20: "PSD AUR",
    21: "UDMR UDMR",
    22: "PSD AUR PNL",
    23: "SOS",
    24: "PNL USR PSD AUR SOS",
    25: "PNL USR",
    26: "PNL PSD AUR",
    27: "PSD PSD",
    28: "AUR PSD UDMR UDMR",
    29: "PSD AUR SOS",
    30: "AUR PSD POT",
    31: "USR AUR PSD PNL SOS",
    32: "UDMR UDMR",
    33: "POT POT",
    34: "PNL AUR USR",
    35: "PNL PSD AUR SOS",
    36: "PSD PSD",
    37: "AUR USR PNL PSD",
    38: "POT SOS",
    39: "USR PSD SOS",
    40: "AUR PSD",
    41: "AUR PSD",
    42: "AUR AUR PNL PNL USR USR USR USR PSD PSD PSD SOS POT",
    43: "USR AUR",
}

CHAMBER = {
    1: "PNL AUR PSD PSD POT",
    2: "PSD PNL PNL USR AUR AUR SOS",
    3: "PSD PSD PSD PNL USR AUR AUR POT SOS",
    4: "PNL USR AUR AUR PSD PSD PSD PSD POT SOS",
    5: "AUR UDMR UDMR PNL PNL PNL PSD SOS POT",
    6: "AUR PSD PSD PNL POT",
    7: "PNL AUR PSD PSD SOS POT",
    8: "USR USR PSD PSD AUR PNL PNL SOS POT",
    9: "AUR PSD PSD PNL SOS",
    10: "PNL AUR USR PSD PSD PSD SOS",
    11: "AUR PNL PSD PSD USR",
    12: "AUR AUR PSD PNL",
    13: "PSD PSD USR USR PNL PNL AUR AUR UDMR POT",
    14: "USR USR PSD PSD PNL PNL AUR AUR AUR SOS POT",
    15: "UDMR UDMR UDMR UDMR",
    16: "PSD PSD PSD USR AUR PNL POT",
    17: "AUR AUR PSD PSD PSD PSD PSD USR PNL POT",
    18: "USR PSD PSD PSD AUR AUR PNL SOS SOS",
    19: "PSD PNL PNL AUR",
    20: "PSD PSD USR PNL AUR",
    21: "UDMR UDMR UDMR UDMR UDMR",
    22: "PSD PSD AUR USR PNL POT",
    23: "PSD AUR SOS SOS",
    24: "PNL PNL USR USR PSD PSD PSD AUR AUR AUR SOS POT",
    25: "PNL PSD USR AUR POT",
    26: "PNL PSD PSD UDMR AUR USR POT",
    27: "PSD PSD AUR PNL",
    28: "AUR USR PSD PNL UDMR UDMR UDMR UDMR",
    29: "USR PSD PSD PNL AUR AUR POT SOS",
    30: "AUR PNL PSD PSD PSD SOS",
    31: "USR USR AUR AUR PSD PSD PSD PNL PNL POT SOS",
    32: "UDMR UDMR PSD AUR PNL",
    33: "AUR UDMR PNL PSD",
    34: "PSD PNL AUR USR SOS POT",
    35: "PNL PSD PSD AUR AUR AUR USR SOS SOS POT",
    36: "AUR AUR AUR PSD PSD",
    37: "AUR AUR USR USR PNL UDMR PSD PSD SOS POT",
    38: "PSD AUR SOS SOS",
    39: "USR AUR UDMR PSD PSD POT SOS",
    40: "AUR AUR PSD PSD USR PNL",
    41: "AUR PNL PSD USR SOS",
    42: "AUR AUR AUR AUR PNL PNL PNL PNL PNL USR USR USR USR USR USR USR USR PSD PSD PSD PSD PSD PSD PSD PSD SOS SOS POT POT",
    43: "USR AUR SOS POT",
}


def won(chamber: str) -> dict[str, dict[str, int]]:
    """The seats of each list by constituency number, for `CD` or `S`."""
    table = CHAMBER if chamber == "CD" else SENATE
    result: dict[str, dict[str, int]] = {}
    for circ, text in table.items():
        seats: dict[str, int] = {}
        for code in text.split():
            seats[LISTS[code]] = seats.get(LISTS[code], 0) + 1
        result[str(circ)] = seats
    return result
