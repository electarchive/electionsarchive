"""Austria's Constituent National Assembly election of 1919 and National Council elections of 1920 to 1930.

The Federal Ministry of the Interior (BMI) keeps the official result publications of the First Republic on its
historical page for National Council elections, one PDF per election, and a summary of the national results of all
five elections with the seats each party won. The Parliament's statistics page on the composition of the National
Council gives each election day and the seats.

- 1919: Tafel IV (registered voters and turnout) and Tafel V (votes of every list) of the statistical office's
  publication on the Constituent National Assembly election give the results of each of the 25 constituencies that
  voted. The seats come from the BMI summary (`nrw_08_abgestimmen1919_1930.pdf`).
- 1920: the BMI's 1920 sheet (`nrw_1920_ergebnisse.pdf`) has a text layer and gives national results only, so the
  national total is the finest unit stored.
- 1923: the BMI's 1923 file prints, for each of the 25 constituencies, the registered voters, the votes cast, and the
  valid votes of four groups of parties (the Christian Social, German nationalist, and Social Democratic groups, and
  the other parties). It labels them preliminary; their national sums equal the final national totals.
- 1927: Tabelle 10 of the 1927 publication gives every list's votes by constituency from the returns of each polling
  place (Einzelausweise fuer Wahlorte). Its national totals equal the BMI summary. Tabelle 13 gives the same
  figures by political district, Vienna by municipal district; they are stored as a second result set that
  supersedes the one by constituency (`parlament_tables/1927-bezirke.txt`).
- 1930: Tabelle 30 of the 1930 special issue of the Statistische Nachrichten gives every list's votes by constituency,
  again from the returns of each polling place, Tabelle 9 the voters, and Tabelle 3 the registered voters.

The scans have no text layer. Their tables were read from the page images and transcribed into
`parlament_tables/{year}.txt`, one file per election. Each row keeps its band on the page (`@y0:y1`) and each table
the edges of its columns, so every stored number points at its page and box. Parsing checks every row and every
printed total: the lists against the valid votes, the constituencies against each state, constituency association,
and national total, and the votes cast less the invalid votes against the valid votes. A mismatch raises, so a
misread number never reaches a package.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.at.bmi import NATIONAL_COUNCIL

SOURCE = "at.parlament"
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))
SCHEME = "at.nr_wahlkreis"
TABLES = Path(__file__).with_name("parlament_tables")
BMI = "https://www.bmi.gv.at/412/nationalratswahlen"
HISTORY = f"{BMI}/historischer_rueckblick.html"
SUMMARY = f"{BMI}/nationalratswahl_2008/files/history/nrw_08_abgestimmen1919_1930.pdf"
PARLIAMENT = "https://www.parlament.gv.at/recherchieren/statistiken/personen-statistiken/zusammensetzung-nr"
FILES = {
    1919: f"{BMI}/files/nrw-1919.pdf",
    1920: f"{BMI}/files/nrw_1920_ergebnisse.pdf",
    1923: f"{BMI}/files/nrw_1923.pdf",
    1927: f"{BMI}/files/nrw_1927.pdf",
    1930: f"{BMI}/files/nrw-1930.pdf",
}

VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
MEASURES = {"registered": REGISTERED, "cast": CAST, "valid": VALID, "invalid": INVALID, "blank": BLANK}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


BMI_SOURCE = key("source", "bmi")
PARLIAMENT_SOURCE = key("source", "parlament")
ASSEMBLY = key("body", "konstituierende-nationalversammlung")


@dataclass(frozen=True)
class Party:
    key: str
    name: str
    kind: str = "party"
    """`party`, `bloc` for a joint list or a group of parties, or `others` for the printed remainder."""


PARTIES = {
    p.key: p
    for p in [
        Party("sdap", "Sozialdemokratische Arbeiterpartei"),
        Party("csp", "Christlichsoziale Partei"),
        Party("noe-bauernbund", "Niederösterreichischer Bauernbund"),
        Party("cs-buerger-arbeiter", "Christlichsoziale Bürger- und Arbeiterpartei"),
        Party("tiroler-bauernbund", "Tiroler Bauernbund"),
        Party("tiroler-volksverein", "Tiroler Volksverein"),
        Party("deutschnationale-1919", "Deutschnationale"),
        Party("tschechoslowaken", "Tschechoslowaken"),
        Party("deutschdemokraten", "Deutschdemokraten"),
        Party("deutsche-volkspartei", "Deutsche Volkspartei"),
        Party("freiheits-ordnungspartei", "Deutsche Freiheits- und Ordnungspartei"),
        Party("buergerliche-demokraten", "Bürgerliche Demokraten"),
        Party("steirische-bauernpartei", "Steirische Bauernpartei"),
        Party("nationaldemokraten", "Nationaldemokraten"),
        Party("kaerntner-bauernbund", "Kärntner Bauernbund"),
        Party("dnsap", "Nationalsozialistische Arbeiterpartei"),
        Party("deutschvoelkischer-wahlausschuss", "Deutschvölkischer Wahlausschuß"),
        Party("demokraten", "Demokraten"),
        Party("demokratische-staendevereinigung", "Demokratische Ständevereinigung"),
        Party("salzburger-bauernbund", "Freiheitlicher Salzburger Bauernbund"),
        Party("juedischnationale", "Jüdischnationale"),
        Party("demokratische-mittelstandspartei", "Demokratische Mittelstandspartei"),
        Party("demokratische-wirtschaftspartei", "Demokratische Wirtschaftspartei"),
        Party("deutschoesterreichische-volkspartei", "Deutschösterreichische Volkspartei"),
        Party("treiplpartei", "Treiplpartei"),
        Party("wirtschaftspolitische-volkspartei", "Wirtschaftspolitische Volkspartei"),
        Party("deutschnationale-parteien", "Deutschnationale Parteien", "bloc"),
        Party("demokratische-parteien", "Demokratische Parteien", "bloc"),
        Party("einheitsliste", "Einheitsliste", "bloc"),
        Party("landbund", "Landbund für Österreich"),
        Party("udeverband", "Udeverband, Bund gegen Korruption"),
        Party("demokratische-liste", "Demokratische Liste"),
        Party("voelkischsozialer-block", "Völkischsozialer Block"),
        Party("nsdap", "Nationalsozialistische Deutsche Arbeiterpartei (Hitlerbewegung)"),
        Party("kpoe", "Kommunistische Partei Österreichs"),
        Party("juedische-partei", "Jüdische Partei"),
        Party("bauern-gewerbetreibende", "Bauern und Gewerbetreibende aller Art Parteien"),
        Party("kleingewerbepartei", "Kleingewerbepartei Österreichs"),
        Party("kaerntner-slowenen", "Partei der Kärntner Slowenen"),
        Party("parteilose-staatsbuerger", "Bund der parteilosen Staatsbürger"),
        Party("schoberblock", "Nationaler Wirtschaftsblock und Landbund (Führung Dr. Schober)", "bloc"),
        Party("heimatblock", "Heimatblock"),
        Party("oevp-1930", "Österreichische Volkspartei"),
        Party("demokratische-mittelpartei", "Demokratische Mittelpartei"),
        Party("juedische-liste", "Jüdische Liste"),
        Party("kaisertreue-volkspartei", "Kaisertreue Volkspartei (Wolff-Verband)"),
        Party("nationaldemokratische-vereinigung", "Nationaldemokratische Vereinigung (Höberthpartei)"),
    ]
}
OTHERS = "andere"


def party_id(code: str) -> str:
    return key("party", code)


@dataclass(frozen=True)
class Election:
    year: int
    day: date
    family: str
    """`assembly` for the Constituent National Assembly, `council` for the National Council."""
    seats: int
    columns: dict[str, str]
    """Column of the transcription (or printed label of the summary) mapped to a party key, or OTHERS."""
    won: dict[str, int]
    stage: str = "official"
    notes: tuple[str, ...] = ()
    sums: tuple[tuple[str, tuple[str, ...]], ...] = ()
    """Columns of the transcription that must equal the sum of other columns in every row and total."""
    labels: dict[str, str] = field(default_factory=dict)
    """Column of the transcription mapped to the label its option is keyed and printed by, when not the party name."""


# Labels of the 1919 lists as the BMI summary prints them, in its order.
LISTS_1919 = {
    "Sozialdemokraten": "sdap",
    "Christlichsoziale": "csp",
    "Niederösterreichischer Bauernbund": "noe-bauernbund",
    "Christlichsoziale Bürger- und Arbeiterpartei": "cs-buerger-arbeiter",
    "Tiroler Bauernbund": "tiroler-bauernbund",
    "Tiroler Volksverein": "tiroler-volksverein",
    "Deutschnationale": "deutschnationale-1919",
    "Tschechoslowaken": "tschechoslowaken",
    "Deutschdemokraten": "deutschdemokraten",
    "Deutsche Volkspartei": "deutsche-volkspartei",
    "Deutsche Freiheits- und Ordnungspartei": "freiheits-ordnungspartei",
    "Bürgerliche Demokraten": "buergerliche-demokraten",
    "Steirische Bauernpartei": "steirische-bauernpartei",
    "Nationaldemokraten": "nationaldemokraten",
    "Kärntner Bauernbund": "kaerntner-bauernbund",
    "Nationalsozialistische Arbeiterpartei": "dnsap",
    "Deutschvölkischer Wahlausschuß": "deutschvoelkischer-wahlausschuss",
    "Demokraten": "demokraten",
    "Demokratische Ständevereinigung": "demokratische-staendevereinigung",
    "Freiheitlicher Salzburger Bauernbund": "salzburger-bauernbund",
    "Jüdischnationale": "juedischnationale",
    "Demokratische Mittelstandspartei": "demokratische-mittelstandspartei",
    "Demokratische Wirtschaftspartei": "demokratische-wirtschaftspartei",
    "Deutschösterreichische Volkspartei": "deutschoesterreichische-volkspartei",
    "Treiplpartei": "treiplpartei",
    "Wirtschaftspolitische Volkspartei": "wirtschaftspolitische-volkspartei",
}

# Columns of the 1919 transcription mapped to the labels of the BMI summary.
CODES_1919 = {
    "SD": "Sozialdemokraten",
    "CS": "Christlichsoziale",
    "NOEBB": "Niederösterreichischer Bauernbund",
    "CSBA": "Christlichsoziale Bürger- und Arbeiterpartei",
    "TBB": "Tiroler Bauernbund",
    "TVV": "Tiroler Volksverein",
    "DNAT": "Deutschnationale",
    "TS": "Tschechoslowaken",
    "DDEM": "Deutschdemokraten",
    "DVP": "Deutsche Volkspartei",
    "DFOP": "Deutsche Freiheits- und Ordnungspartei",
    "BD": "Bürgerliche Demokraten",
    "STBP": "Steirische Bauernpartei",
    "ND": "Nationaldemokraten",
    "KBB": "Kärntner Bauernbund",
    "NSAP": "Nationalsozialistische Arbeiterpartei",
    "DVW": "Deutschvölkischer Wahlausschuß",
    "DEM": "Demokraten",
    "DSV": "Demokratische Ständevereinigung",
    "FSBB": "Freiheitlicher Salzburger Bauernbund",
    "JN": "Jüdischnationale",
    "DMP": "Demokratische Mittelstandspartei",
    "DWP": "Demokratische Wirtschaftspartei",
    "DOEVP": "Deutschösterreichische Volkspartei",
    "TREIPL": "Treiplpartei",
    "WPVP": "Wirtschaftspolitische Volkspartei",
}
SUMS_1919 = (
    ("registered", ("reg_m", "reg_f")),
    ("cast", ("cast_m", "cast_f")),
    ("cast", ("ballots",)),
    ("ballots", ("valid", "invalid", "blank")),
    ("valid_check", ("valid",)),
    ("CSALL", ("CS", "CSBA", "NOEBB", "TVV", "TBB")),
    (
        "DNALL",
        (
            "DNAT", "ND", "DVW", "DVP", "DSV", "DDEM", "NSAP", "DWP", "DOEVP", "DFOP", "FSBB", "STBP", "KBB", "BD",
            "DEM", "DMP", "WPVP",
        ),
    ),
)  # fmt: skip

ELECTIONS = {
    1919: Election(
        1919,
        date(1919, 2, 16),
        "assembly",
        170,
        {code: LISTS_1919[label] for code, label in CODES_1919.items()},
        {},
        sums=SUMS_1919,
        labels=CODES_1919,
        notes=(
            (
                "On 16 February 1919, 159 members were elected; on 4 April 1919 the Assembly summoned 11 more for the "
                "constituencies whose voters could not take part. The BMI summary counts all 170 seats by party, and the "
                "seats are stored as it prints them. The votes are those of the statistical office's publication, whose "
                "national totals differ slightly from the summary's: the summary counts 3,554,242 registered voters, "
                "2,998,297 votes cast, and 2,973,454 valid votes, and, for example, 1,211,814 votes for the Social "
                "Democrats against the publication's 1,211,393."
            ),
            (
                "Constituencies 12 (Znaimer Kreis), 18 (Böhmerwaldgau), and 28 to 38 (Deutschböhmen, Sudetenland, and "
                "the enclaves of Brünn, Iglau, and Olmütz) could not vote. In Mittel- und Untersteier only the part "
                "under Austrian control voted, in Carinthia the court districts of Tarvis, Eisenkappel, Bleiburg, and "
                "Eberndorf did not vote, and of Deutsch-Südtirol only the political district of Lienz voted."
            ),
            (
                "The publication counts some lists under the column of a related party and names them in footnotes: "
                "in Viertel ober dem Wienerwald the Deutschvölkische Partei (Deutschnationale), in Viertel unter dem "
                "Manhartsberg 30 votes of the Unabhängige christlich-republikanische Arbeiterpartei (Christlichsoziale), "
                "in Graz und Umgebung the Schutzverband deutscharischer Kriegsteilnehmer (Deutschnationale), in Lienz "
                "the Tiroler Volkspartei (Tiroler Volksverein) and the Deutschfreiheitliche Partei (Deutschnationale), "
                "in Vorarlberg the Christlichsoziale Volkspartei, in Wien West the joint list of the Nationaldemokraten "
                "and Deutschnationale (Nationaldemokraten), in Wien West and Viertel unter dem Wienerwald the "
                "Bürgerliche Demokraten und Wirtschaftspartei der Festbesoldeten, in the Traunviertel the Deutsche "
                "Freiheits- und Ordnungspartei und völkischer Wirtschaftsbund, and in Salzburg the Deutsche "
                "Arbeiterpartei (Nationalsozialistische Arbeiterpartei). Each is stored under that column. The "
                "Deutschnationale of Viertel unter dem Manhartsberg include 864 votes of the Treipl-Partei, which are "
                "stored as the Treiplpartei."
            ),
            ("In Oststeier the invalid votes include the blank ballot papers, which are not printed separately."),
            (
                "The BMI summary prints the five Christian Social lists (Christlichsoziale, Niederösterreichischer "
                "Bauernbund, Christlichsoziale Bürger- und Arbeiterpartei, Tiroler Bauernbund, and Tiroler Volksverein) "
                "both separately and together (1,068,382 votes and 69 seats); each list is stored as its own option."
            ),
        ),
    ),
    1920: Election(
        1920,
        date(1920, 10, 17),
        "council",
        183,
        {
            "Christlichsoziale": "csp",
            "Sozialdemokraten": "sdap",
            "Deutschnationale Parteien": "deutschnationale-parteien",
            "Demokratische Parteien": "demokratische-parteien",
            "Andere Parteien": OTHERS,
        },
        {},
        notes=(
            (
                "Carinthia voted on 19 June 1921 and Burgenland on 18 June 1922; the BMI counts their votes and seats in "
                "this election. For Salzburg and the district of Mattersburg the votes cast are the valid votes, because "
                "no figure for the votes cast was reported."
            ),
            (
                "The BMI prints the parties in groups. German nationalist parties: the Greater German People's Party, "
                "the German Austrian Peasants' Party, the Carinthian Peasants' League, and the National Socialists. "
                "Democratic parties: the Civic Labour Party, the Democrats, and the Burgenland Citizens' and Peasants' "
                "Party. Other parties: the Christian National Unity List, the Communists, the Jewish Nationals, the "
                "Socialists and Democratic Czechoslovaks, and the Carinthian Slovenes."
            ),
        ),
    ),
    1923: Election(
        1923,
        date(1923, 10, 21),
        "council",
        165,
        {"CS": "csp", "DN": "deutschnationale-parteien", "SD": "sdap", "AND": OTHERS},
        {"csp": 82, "deutschnationale-parteien": 15, "sdap": 68},
        stage="preliminary",
        notes=(
            (
                "The BMI's 1923 file prints results by constituency for four groups of parties and labels them "
                "preliminary; their national sums equal the final national totals of the BMI summary. The German "
                "nationalist group is the Association of the Greater Germans and the Landbund (259,375 votes, 10 seats), "
                "the Landbund für Österreich (76,441 votes, 5 seats), the Burgenland Peasants' League (23,142 votes), "
                "and the German nationalist share (63,642 votes) of the Carinthian Unity List, whose Christian Social "
                "share (31,823 votes) is counted with the Christian Social Party. The other parties are the Jewish "
                "Electoral Union, the Communist Party, the Civic Democratic Labour Party, the Carinthian Slovenes, the "
                "Czechoslovak Minority Party, the Croat Party, the Imperial Loyalist People's Party, and the League of "
                "All Creators."
            ),
        ),
    ),
    1927: Election(
        1927,
        date(1927, 4, 24),
        "council",
        165,
        {
            "EL": "einheitsliste",
            "SD": "sdap",
            "LB": "landbund",
            "UDE": "udeverband",
            "DEM": "demokratische-liste",
            "VSB": "voelkischsozialer-block",
            "NSDAP": "nsdap",
            "KPOE": "kpoe",
            "JUED": "juedische-partei",
            "BAUERN": "bauern-gewerbetreibende",
            "KLEIN": "kleingewerbepartei",
            "SLOW": "kaerntner-slowenen",
            "BUND": "parteilose-staatsbuerger",
        },
        {"einheitsliste": 85, "sdap": 71, "landbund": 9},
        notes=(
            (
                "The Unity List (Einheitsliste) was the joint list of the Christian Social Party and the Greater German "
                "People's Party; of its 85 seats, 73 went to Christian Socials and 12 to Greater Germans."
            ),
            (
                "The constituency returns of the electoral boards (Tabelle 2 of the same publication) give slightly "
                "different figures: 4,114,940 registered, 3,673,639 votes cast, and 3,636,712 valid votes."
            ),
        ),
    ),
    1930: Election(
        1930,
        date(1930, 11, 9),
        "council",
        165,
        {
            "SD": "sdap",
            "CS": "csp",
            "NWB": "schoberblock",
            "HB": "heimatblock",
            "NSDAP": "nsdap",
            "LB": "landbund",
            "KPOE": "kpoe",
            "OEVP": "oevp-1930",
            "DMP": "demokratische-mittelpartei",
            "JUED": "juedische-liste",
            "KAISER": "kaisertreue-volkspartei",
            "NATDEM": "nationaldemokratische-vereinigung",
        },
        {"sdap": 72, "csp": 66, "schoberblock": 19, "heimatblock": 8},
        notes=(
            (
                "The votes come from the returns of each polling place. The returns of the electoral boards, which the "
                "BMI summary prints, count 3,716,166 voters and 3,688,068 valid votes; the polling-place returns count "
                "3,715,372 voters and 3,687,082 valid votes. The invalid votes are not printed by constituency."
            ),
            (
                "The Christian Social Party ran in Vienna as the Christian Social Party and Heimatwehr and in Lower "
                "Austria as the Christian Social Party and Heimwehr; the publication counts the three lists together."
            ),
        ),
    ),
}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{slug(e)}" for e in ELECTIONS.values())]


def slug(e: Election) -> str:
    return f"{'knv' if e.family == 'assembly' else 'nr'}-{e.year}"


def fetch() -> None:
    raw.fetch(SOURCE, SUMMARY, suffix=".pdf")
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, PARLIAMENT, suffix=".html")
    raw.fetch(SOURCE, HISTORY, suffix=".html")


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS.values():
        election(e).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------- transcriptions


@dataclass
class Row:
    code: str
    band: tuple[float, float]
    values: dict[str, int | None]
    splits: dict[str, tuple[str, int]] = field(default_factory=dict)
    """Column mapped to the list and votes a footnote says the cell includes."""


@dataclass
class Table:
    page: int
    columns: list[str]
    edges: list[tuple[float, float]]
    rows: list[Row] = field(default_factory=list)
    totals: list[tuple[str, range, dict[str, int | None]]] = field(default_factory=list)


def _number(text: str) -> int | None:
    return None if text == "-" else int(text)


SPLIT = re.compile(r"^(\d+)\[([A-Z]+):(\d+)\]$")


def _cells(columns: list[str], texts: list[str]) -> tuple[dict[str, int | None], dict[str, tuple[str, int]]]:
    values: dict[str, int | None] = {}
    splits: dict[str, tuple[str, int]] = {}
    for column, text in zip(columns, texts, strict=True):
        found = SPLIT.match(text)
        if found:
            values[column] = int(found[1])
            splits[column] = (found[2], int(found[3]))
        else:
            values[column] = _number(text)
    return values, splits


@cache
def tables(year: int) -> list[Table]:
    found: list[Table] = []
    for line in (TABLES / f"{year}.txt").read_text().splitlines():
        words = line.split("#", 1)[0].split()
        if not words:
            continue
        head, rest = words[0], words[1:]
        if head == "table":
            found.append(Table(int(rest[0].removeprefix("page=")), [], []))
        elif head == "columns":
            found[-1].columns = rest
        elif head == "x":
            edges = [float(x) for x in rest]
            found[-1].edges = list(zip(edges[::2], edges[1::2], strict=True))
        elif head == "row":
            y0, y1 = rest[1].removeprefix("@").split(":")
            values, splits = _cells(found[-1].columns, rest[2:])
            found[-1].rows.append(Row(rest[0], (float(y0), float(y1)), values, splits))
        elif head == "total":
            first, last = rest[1].split("-")
            values = dict(zip(found[-1].columns, (_number(x) for x in rest[2:]), strict=True))
            found[-1].totals.append((rest[0], range(int(first), int(last) + 1), values))
        else:
            raise ValueError(f"{year}.txt: unknown line {line!r}")
    return found


def _check(year: int, where: str, values: dict[str, int | None], parties: list[str]) -> None:
    for column, parts in ELECTIONS[year].sums:
        if column in values and all(part in values for part in parts):
            summed = sum(values[part] or 0 for part in parts)
            if summed != (values[column] or 0):
                raise ValueError(f"{year} {where}: {'+'.join(parts)} add up to {summed}, {column} is {values[column]}")
    valid = values.get("valid")
    if valid is not None:
        listed = sum(values.get(column) or 0 for column in parties)
        if listed != valid:
            raise ValueError(f"{year} {where}: the lists add up to {listed}, the valid votes are {valid}")
    cast, invalid = values.get("cast"), values.get("invalid")
    blank = values.get("blank") or 0
    if valid is not None and cast is not None and invalid is not None and cast - invalid - blank != valid:
        raise ValueError(f"{year} {where}: {cast} cast less {invalid} invalid and {blank} blank is not {valid} valid")


@cache
def constituencies(year: int) -> tuple[dict[str, dict[str, int | None]], dict[str, int | None]]:
    """Each constituency's figures and the national figures, checked against every total the tables print."""
    e = ELECTIONS[year]
    units: dict[str, dict[str, int | None]] = {}
    national: dict[str, int | None] = {}
    for table in tables(year):
        rows = {row.code: row for row in table.rows}
        for row in table.rows:
            units.setdefault(row.code, {}).update(row.values)
        for name, span, printed in table.totals:
            for column in table.columns:
                summed = sum(rows[str(n)].values[column] or 0 for n in span if str(n) in rows)
                if summed != (printed[column] or 0):
                    raise ValueError(
                        f"{year} total {name}, {column}: rows add up to {summed}, printed {printed[column]}"
                    )
            if name == "AT":
                national.update(printed)
    for code, values in units.items():
        _check(year, f"constituency {code}", values, list(e.columns))
    _check(year, "Austria", national, list(e.columns))
    return units, national


def summary_lines(path: Path) -> list[str]:
    return subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()


FIGURE = re.compile(
    r"^\s*(?P<label>[^\d.]+?)\s*\.*\s*(?P<votes>\d{1,3}(?:\.\d{3})*)(?:\s+(?P<share>\d+(?:,\d+)?))?(?:\s+(?P<seats>\d+|-))?\s*$"
)


@cache
def national_text(year: int) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Totals, votes, and seats of 1919 or 1920 from the BMI's text-layer sheets."""
    if year == 1919:
        lines = summary_lines(raw.cached(SOURCE, SUMMARY).path)
        start = next(i for i, line in enumerate(lines) if "16. Februar 1919" in line)
        end = next(i for i, line in enumerate(lines) if "17. Oktober 1920" in line)
        lines = lines[start + 1 : end]
    else:
        lines = summary_lines(raw.cached(SOURCE, FILES[1920]).path)
    totals: dict[str, int] = {}
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    labels = LISTS_1919 if year == 1919 else ELECTIONS[year].columns
    for line in lines:
        found = FIGURE.match(line)
        if found is None:
            continue
        label = found["label"].strip(" .")
        value = int(found["votes"].replace(".", ""))
        if label.startswith("Wahlberechtigte"):
            totals["registered"] = value
        elif label.startswith("Abgegebene"):
            totals["cast"] = value
        elif label.startswith("Gültige"):
            totals["valid"] = value
            totals["seats"] = int(found["seats"] or 0)
        elif label in labels:
            votes[label] = value
            if found["seats"] and found["seats"] != "-":
                seats[label] = int(found["seats"])
    if set(votes) != set(labels):
        raise ValueError(f"{year}: the summary lacks {sorted(set(labels) - set(votes))}")
    if sum(votes.values()) != totals["valid"]:
        raise ValueError(f"{year}: the lists add up to {sum(votes.values())}, the valid votes are {totals['valid']}")
    if sum(seats.values()) != totals["seats"] or totals["seats"] != ELECTIONS[year].seats:
        raise ValueError(f"{year}: the seats add up to {sum(seats.values())}, printed {totals['seats']}")
    return totals, votes, seats


# ---------------------------------------------------------------- packages

NAMES_1923 = [
    "Wien-Innen-Ost",
    "Wien-Innen-West",
    "Wien-Nordwest",
    "Wien-Nordost",
    "Wien-Südost",
    "Wien-Südwest",
    "Wien-West",
    "Viertel ober dem Wienerwald",
    "Viertel unter dem Wienerwald",
    "Viertel ober dem Manhartsberg",
    "Viertel unter dem Manhartsberg",
    "Linz und Umgebung",
    "Innviertel",
    "Hausruckviertel",
    "Traunviertel",
    "Mühlviertel",
    "Salzburg",
    "Tirol",
    "Vorarlberg",
    "Graz und Umgebung",
    "Mittel- und Untersteier",
    "Oststeier",
    "Obersteier",
    "Kärnten",
    "Burgenland",
]
PLAN = date(1923, 10, 21)
"""The 25 constituencies of the National Council Election Act of 1923, used in 1923, 1927, and 1930."""


# The 38 constituencies of the Election Act for the Constituent National Assembly of 18 December 1918, as Tafel III of
# the 1919 publication names them. It names the constituencies of Deutschböhmen (28 to 33), Sudetenland (34 and 35),
# and the enclaves (36 to 38) only as groups.
NAMES_1919 = {
    1: "Wien Innen-Ost",
    2: "Wien Innen-West",
    3: "Wien Nordwest",
    4: "Wien Nordost",
    5: "Wien Südost",
    6: "Wien Südwest",
    7: "Wien West",
    8: "Viertel oberm Wienerwald",
    9: "Viertel unterm Wienerwald",
    10: "Viertel oberm Manhartsberg",
    11: "Viertel unterm Manhartsberg",
    12: "Znaimer Kreis",
    13: "Linz und Umgebung",
    14: "Innviertel",
    15: "Hausruckviertel",
    16: "Traunviertel",
    17: "Mühlviertel",
    18: "Böhmerwaldgau",
    19: "Salzburg",
    20: "Graz und Umgebung",
    21: "Mittel- und Untersteier",
    22: "Oststeier",
    23: "Obersteier",
    24: "Kärnten",
    25: "Nordtirol",
    26: "Deutsch-Südtirol",
    27: "Vorarlberg",
    **{n: f"Deutschböhmen, Wahlkreis {n}" for n in range(28, 34)},
    **{n: f"Sudetenland, Wahlkreis {n}" for n in (34, 35)},
    **{n: f"Einschlußgebiete, Wahlkreis {n}" for n in range(36, 39)},
}
PLAN_1919 = date(1918, 12, 18)
END_1919 = date(1920, 10, 17)
"""The 1919 constituencies served until the National Council election of 1920, held under a new election act."""
# The units of 1919 as Tafel V names them; in 21, 24, and 26 only part of the constituency voted.
UNITS_1919 = {
    **{n: NAMES_1919[n] for n in NAMES_1919 if n <= 27 and n not in (12, 18)},
    21: "Mittelsteier",
    26: "Politischer Bezirk Lienz",
}
PARTIAL_1919 = {21, 24, 26}


def area(number: int, year: int = 1923) -> str:
    return key("area", SCHEME, PLAN_1919 if year == 1919 else PLAN, number)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 38 constituencies of the Constituent National Assembly election of 1919 and the 25 National Council "
        "constituencies of Austria from 1923 to 1930",
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=AUSTRIA,
        kind="electoral_district",
        rank=1,
        noun="National Council constituency",
        code_system=None,
    )
    for number, text in enumerate(NAMES_1923, start=1):
        id = area(number)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=f"1923:{number}", valid_from=PLAN, valid_to=date(1934, 5, 1))
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=PLAN, valid_to=date(1934, 5, 1))
        p.name(id, text, "de", "official")
    for number, text in NAMES_1919.items():
        id = area(number, 1919)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=f"1919:{number}", valid_from=PLAN_1919, valid_to=END_1919)
        if number <= 27:
            p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=PLAN_1919, valid_to=END_1919)
        p.name(id, text, "de", "official")
    return p


def _artifacts() -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url) for url in (SUMMARY, *FILES.values())]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Constituent National Assembly of 1919 and the parties of the Austrian elections from 1919 to 1930",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(AUSTRIA)
    p.add(
        "source",
        id=BMI_SOURCE,
        kind="official_body",
        publisher="Bundesministerium für Inneres",
        title="Nationalratswahlen: Historischer Rückblick, Ergebnisse 1919 bis 1930",
        url=HISTORY,
        attribution="Bundesministerium für Inneres",
    )
    p.add(
        "source",
        id=PARLIAMENT_SOURCE,
        kind="official_body",
        publisher="Parlament Österreich",
        title="Zusammensetzung des Nationalrats",
        url=PARLIAMENT,
        attribution="Parlament Österreich",
    )
    for artifact in _artifacts():
        p.artifact(artifact, BMI_SOURCE)
    p.artifact(raw.cached(SOURCE, HISTORY), BMI_SOURCE)
    p.artifact(raw.cached(SOURCE, PARLIAMENT), PARLIAMENT_SOURCE)
    p.add("body", id=ASSEMBLY, polity_id=AUSTRIA, kind="constituent_assembly")
    p.name(ASSEMBLY, "Constituent National Assembly", "en", "official")
    p.name(ASSEMBLY, "Konstituierende Nationalversammlung", "de", "native", False)
    for spec in PARTIES.values():
        id = party_id(spec.key)
        p.add("party", id=id, kind=spec.kind, polity_id=AUSTRIA)
        p.add("external_id", entity_id=id, scheme="at.parlament.party", value=spec.key)
        p.name(id, spec.name, "de", "official")
    return p


def artifact_id(url: str) -> str:
    stored = raw.cached(SOURCE, url)
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{BMI_SOURCE}\x1f{stored.storage_key}"))


def _pages(stored: raw.Artifact) -> dict[int, tuple[int, int]]:
    out = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "9999", str(stored.path)], capture_output=True, text=True, check=True
    ).stdout
    return {
        int(found.group(1)): (round(float(found.group(2))), round(float(found.group(3))))
        for found in re.finditer(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", out)
    }


METHOD_TEXT = (
    "National results from the Federal Ministry of the Interior's summary of the elections of 1919 to 1930 "
    "({file}), which has a text layer. The summary gives no figures below the national level, so the national total "
    "is the only unit stored."
)
METHOD_SCAN = (
    "Transcribed from the scanned tables of the Federal Ministry of the Interior's publication on this election "
    "(pages {pages} of {file}). Every number was checked against the totals the tables print: the lists against the "
    "valid votes, the constituencies against each printed state or constituency-association total and the national "
    "total, and the votes cast less the invalid votes against the valid votes. The seats are those the BMI summary "
    "of 1919 to 1930 prints; they were allocated in the constituencies and then in the four constituency "
    "associations (Wahlkreisverbände), so they are stored only for Austria as a whole."
)
METHOD_1919 = (
    "Transcribed from the scanned tables of the statistical office's publication on this election (Beiträge zur "
    "Statistik Deutschösterreichs, 1. Heft; pages {pages} of {file}, published by the Federal Ministry of the "
    "Interior): Tafel IV for the registered voters and turnout and Tafel V for the votes of every list. Every number "
    "was checked against the totals the tables print: the lists against the valid votes and against the printed "
    "Christian Social and German national group totals, men and women against both sexes, the valid, invalid, and "
    "blank ballot papers against the votes cast, and the constituencies against each printed state total and the "
    "national total. The seats are those the BMI summary of 1919 to 1930 prints for Austria as a whole."
)


def election(e: Election) -> Package:
    assembly = e.family == "assembly"
    noun = "Constituent National Assembly election" if assembly else "National Council election"
    native = "Wahl zur Konstituierenden Nationalversammlung" if assembly else "Nationalratswahl"
    scanned = e.year in (1919, 1923, 1927, 1930)
    p = Package(
        f"{SOURCE}/{slug(e)}",
        SOURCE,
        f"The {e.year} Austrian {noun} "
        + ("by constituency" if scanned else "nationally")
        + (" and political district" if e.year in DISTRICT_YEARS else "")
        + " from the Federal Ministry of the Interior's historical results",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/registry",
            *([] if assembly else ["at.bmi/registry"]),
            *(["at.bmi/geo"] if e.year in DISTRICT_YEARS else []),
        ],
    )
    p.external |= {AUSTRIA, COUNTRY}
    url = FILES[e.year] if scanned else (FILES[1920] if e.year == 1920 else SUMMARY)
    stored = raw.cached(SOURCE, url)
    p.uses(stored)
    if not scanned or e.year == 1919:
        p.uses(raw.cached(SOURCE, SUMMARY))
    p.uses(raw.cached(SOURCE, PARLIAMENT))
    election_id = key("election", e.year)
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRIA,
        kind="general",
        date_from=e.day,
        date_to=e.day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{e.year} Austrian {noun}", "en", "official")
    p.name(election_id, f"{native} {e.year}", "de", "native", False)
    path = f"at/{e.day}-{'constituent-assembly' if assembly else 'national-council'}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", e.year)
    title = "Constituent National Assembly" if assembly else "National Council"
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=ASSEMBLY if assembly else NATIONAL_COUNCIL,
        district_area_id=COUNTRY,
        magnitude=e.seats,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{e.year} {title}", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
    result_set = key("result_set", e.year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage=e.stage,
        kind="counted",
        source_id=BMI_SOURCE,
        status="active",
        is_default=e.year not in DISTRICT_YEARS,
        completeness=1.0,
    )
    file = url.rsplit("/", 1)[1]
    if scanned:
        pages = sorted({table.page for table in tables(e.year)})
        method = (METHOD_1919 if e.year == 1919 else METHOD_SCAN).format(
            pages=", ".join(str(n) for n in pages), file=file
        )
    else:
        method = METHOD_TEXT.format(file=file)
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    for index, text in enumerate(e.notes):
        p.add("note", id=key("note", result_set, index), entity_id=result_set, kind="caveat", body_md=text, lang="en")

    options: dict[str, str] = {}

    def option(column: str, label: str, position: int) -> str:
        code = e.columns[column]
        id = options[column] = key("option", e.year, e.labels.get(column, column))
        if code == OTHERS:
            p.add(
                "contest_option", id=id, contest_id=contest, kind="others", ballot_position=position, ballot_label=label
            )
        else:
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="list",
                party_id=party_id(code),
                ballot_position=position,
                ballot_label=label,
            )
        return id

    if not scanned:
        totals, votes, seats = national_text(e.year)
        for position, label in enumerate(e.columns, start=1):
            option(label, label, position)
        unit = key("unit", e.year, "AT")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code=SCHEME,
            code=f"{e.day}:AT",
            area_relation="equals",
            area_id=COUNTRY,
            kind="standard",
        )
        p.name(unit, "Österreich", "de", "official")
        figures: dict[str, int] = {k: v for k, v in totals.items() if k in MEASURES}
        for column, value in figures.items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=MEASURES[column], value=value)
            _value(p, result_set, unit, None, MEASURES[column], value)
        for label, value in votes.items():
            p.add("declared", result_set_id=result_set, option_id=options[label], measure=VOTES, value=value)
            _value(p, result_set, unit, options[label], VOTES, value)
        for label, n in seats.items():
            p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=n)
        return p

    units, national = constituencies(e.year)
    stored_columns = {*e.columns, *MEASURES}
    splits: dict[str, int] = {}
    for table in tables(e.year):
        for row in table.rows:
            for column, (code, n) in row.splits.items():
                splits[column] = splits.get(column, 0) + n
                splits[code] = splits.get(code, 0) - n
    national = {
        column: value - splits.get(column, 0) if value is not None else None for column, value in national.items()
    }
    national |= {code: -n for code, n in splits.items() if code not in national}
    if e.labels:
        order = list(e.labels)
    else:
        order = sorted(e.columns, key=lambda column: -(national.get(column) or 0))
    for position, column in enumerate(order, start=1):
        code = e.columns[column]
        label = e.labels.get(column) or ("Andere Parteien" if code == OTHERS else PARTIES[code].name)
        option(column, label, position)
    if e.labels:
        seats = national_text(e.year)[2]
        won = {column: seats[label] for column, label in e.labels.items() if label in seats}
    else:
        won = {column: e.won[e.columns[column]] for column in e.columns if e.columns[column] in e.won}
    if sum(won.values()) != e.seats:
        raise ValueError(f"{e.year}: the seats add up to {sum(won.values())}, not {e.seats}")
    for column, n in won.items():
        p.add("outcome", result_set_id=result_set, option_id=options[column], kind="won", seats=n)
    for column, value in national.items():
        if value is None or column not in stored_columns:
            continue
        if column in MEASURES:
            p.add("declared", result_set_id=result_set, option_id=None, measure=MEASURES[column], value=value)
        else:
            p.add("declared", result_set_id=result_set, option_id=options[column], measure=VOTES, value=value)
    sizes = _pages(stored)
    page_ids: dict[int, str] = {}
    for number in sorted({table.page for table in tables(e.year)}):
        width, height = sizes[number]
        page_ids[number] = key("page", file, number)
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact_id(url),
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}/{number}.png",
            width=width,
            height=height,
        )
    unit_ids: dict[str, str] = {}
    for code in sorted(units, key=int):
        unit = unit_ids[code] = key("unit", e.year, code)
        partial = e.year == 1919 and int(code) in PARTIAL_1919
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code=SCHEME,
            code=f"{e.day}:{code}",
            area_relation="within" if partial else "equals",
            area_id=area(int(code), e.year),
            kind="standard",
        )
        name = UNITS_1919[int(code)] if e.year == 1919 else NAMES_1923[int(code) - 1]
        p.name(unit, name, "de", "official")
    for table in tables(e.year):
        for row in table.rows:
            for column, (x0, x1) in zip(table.columns, table.edges, strict=True):
                value = row.values[column]
                if value is None or column not in stored_columns:
                    continue
                parts = [(column, value)]
                if column in row.splits:
                    code, n = row.splits[column]
                    parts = [(column, value - n), (code, n)]
                for part, number in parts:
                    if part in MEASURES:
                        option_id, measure = None, MEASURES[part]
                    else:
                        option_id, measure = options[part], VOTES
                    _value(p, result_set, unit_ids[row.code], option_id, measure, number)
                    p.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=unit_ids[row.code],
                        option_id=option_id,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        page_id=page_ids[table.page],
                        source_id=None,
                        x0=x0,
                        y0=row.band[0],
                        x1=x1,
                        y1=row.band[1],
                    )
    if e.year in DISTRICT_YEARS:
        districts(p, e, election_id, contest, result_set, options, won, national, page_ids, stored)
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


# ---------------------------------------------------------------- political districts

DISTRICT_YEARS = (1927,)
DISTRICT_PAGES = {1927: "Tabelle 13"}


@dataclass(frozen=True)
class District:
    name: str
    land: str
    """The state's digit in the BMI's codes (1 Burgenland to 9 Vienna)."""
    successors: tuple[str, ...]
    """The present political districts (BMI codes) that cover most of the district's territory."""


def _d(name: str, land: str, *successors: str) -> District:
    return District(name, land, successors)


# Vienna's 21 municipal districts of 1927 and the present districts that cover them: Hietzing then included most of
# Penzing (14), Rudolfsheim and Fünfhaus now form Rudolfsheim-Fünfhaus (15), and Floridsdorf included part of
# Donaustadt (22).
VIENNA_1927 = [
    ("I", "Innere Stadt", ("901",)), ("II", "Leopoldstadt", ("902",)), ("III", "Landstraße", ("903",)),
    ("IV", "Wieden", ("904",)), ("V", "Margareten", ("905",)), ("VI", "Mariahilf", ("906",)),
    ("VII", "Neubau", ("907",)), ("VIII", "Josefstadt", ("908",)), ("IX", "Alsergrund", ("909",)),
    ("X", "Favoriten", ("910",)), ("XI", "Simmering", ("911",)), ("XII", "Meidling", ("912",)),
    ("XIII", "Hietzing", ("913", "914")), ("XIV", "Rudolfsheim", ("915",)), ("XV", "Fünfhaus", ("915",)),
    ("XVI", "Ottakring", ("916",)), ("XVII", "Hernals", ("917",)), ("XVIII", "Währing", ("918",)),
    ("XIX", "Döbling", ("919",)), ("XX", "Brigittenau", ("920",)), ("XXI", "Floridsdorf", ("921", "922")),
]  # fmt: skip

DISTRICTS_1927: dict[str, District] = {
    **{
        f"wien-{n:02d}": District(f"{roman}. {text}", "9", successors)
        for n, (roman, text, successors) in enumerate(VIENNA_1927, start=1)
    },
    "wiener-neustadt-stadt": _d("Wiener Neustadt (Stadt)", "3", "304"),
    "st-poelten-stadt": _d("St. Pölten (Stadt)", "3", "302"),
    "waidhofen-ybbs": _d("Waidhofen an der Ybbs (Stadt)", "3", "303"),
    "amstetten": _d("Amstetten", "3", "305"),
    "baden": _d("Baden", "3", "306"),
    "bruck-leitha": _d("Bruck an der Leitha", "3", "307"),
    "floridsdorf-umgebung": _d("Floridsdorf Umgebung", "3", "922", "324"),
    "gaenserndorf": _d("Gänserndorf", "3", "308"),
    "gmuend": _d("Gmünd", "3", "309"),
    "hietzing-umgebung": _d("Hietzing Umgebung", "3", "923", "324"),
    "horn": _d("Horn", "3", "311"),
    "korneuburg": _d("Korneuburg", "3", "312"),
    "krems": _d("Krems", "3", "301", "313"),
    "lilienfeld": _d("Lilienfeld", "3", "314"),
    "melk": _d("Melk", "3", "315"),
    "mistelbach": _d("Mistelbach", "3", "316"),
    "moedling": _d("Mödling", "3", "317"),
    "neunkirchen": _d("Neunkirchen", "3", "318"),
    "oberhollabrunn": _d("Ober-Hollabrunn", "3", "310"),
    "poeggstall": _d("Pöggstall", "3", "315", "313"),
    "st-poelten-land": _d("St. Pölten", "3", "319"),
    "scheibbs": _d("Scheibbs", "3", "320"),
    "tulln": _d("Tulln", "3", "321", "324"),
    "waidhofen-thaya": _d("Waidhofen an der Thaya", "3", "322"),
    "wiener-neustadt-land": _d("Wiener Neustadt", "3", "323"),
    "zwettl": _d("Zwettl", "3", "325"),
    "linz-stadt": _d("Linz (Stadt)", "4", "401"),
    "steyr-stadt": _d("Steyr (Stadt)", "4", "402"),
    "braunau": _d("Braunau am Inn", "4", "404"),
    "eferding": _d("Eferding", "4", "405"),
    "freistadt": _d("Freistadt", "4", "406"),
    "gmunden": _d("Gmunden", "4", "407"),
    "grieskirchen": _d("Grieskirchen", "4", "408"),
    "kirchdorf": _d("Kirchdorf an der Krems", "4", "409"),
    "linz-land": _d("Linz", "4", "410"),
    "perg": _d("Perg", "4", "411"),
    "ried": _d("Ried im Innkreis", "4", "412"),
    "rohrbach": _d("Rohrbach", "4", "413"),
    "schaerding": _d("Schärding", "4", "414"),
    "steyr-umgebung": _d("Steyr Umgebung", "4", "415"),
    "urfahr-umgebung": _d("Urfahr Umgebung", "4", "416"),
    "voecklabruck": _d("Vöcklabruck", "4", "417"),
    "wels": _d("Wels", "4", "403", "418"),
    "salzburg-stadt": _d("Salzburg (Stadt)", "5", "501"),
    "hallein": _d("Hallein", "5", "502"),
    "salzburg-umgebung": _d("Salzburg", "5", "503"),
    "st-johann-pongau": _d("St. Johann", "5", "504"),
    "tamsweg": _d("Tamsweg", "5", "505"),
    "zell-am-see": _d("Zell am See", "5", "506"),
    "innsbruck-stadt": _d("Innsbruck (Stadt)", "7", "701"),
    "imst": _d("Imst", "7", "702"),
    "innsbruck-land": _d("Innsbruck", "7", "703"),
    "kitzbuehel": _d("Kitzbühel", "7", "704"),
    "kufstein": _d("Kufstein", "7", "705"),
    "landeck": _d("Landeck", "7", "706"),
    "lienz": _d("Lienz", "7", "707"),
    "reutte": _d("Reutte", "7", "708"),
    "schwaz": _d("Schwaz", "7", "709"),
    "bludenz": _d("Bludenz", "8", "801"),
    "bregenz": _d("Bregenz", "8", "802", "803"),
    "feldkirch": _d("Feldkirch", "8", "804", "803"),
    "graz-stadt": _d("Graz (Stadt)", "6", "601"),
    "bruck-mur": _d("Bruck an der Mur", "6", "602"),
    "deutschlandsberg": _d("Deutschlandsberg", "6", "603"),
    "feldbach": _d("Feldbach", "6", "604", "605"),
    "graz-umgebung": _d("Graz", "6", "606"),
    "groebming": _d("Gröbming", "6", "612"),
    "hartberg": _d("Hartberg", "6", "607", "605"),
    "judenburg": _d("Judenburg", "6", "608", "609"),
    "leibnitz": _d("Leibnitz", "6", "610"),
    "leoben": _d("Leoben", "6", "611"),
    "liezen": _d("Liezen", "6", "612"),
    "muerzzuschlag": _d("Mürzzuschlag", "6", "613"),
    "murau": _d("Murau", "6", "614"),
    "radkersburg": _d("Radkersburg", "6", "615"),
    "voitsberg": _d("Voitsberg", "6", "616"),
    "weiz": _d("Weiz", "6", "617"),
    "klagenfurt-stadt": _d("Klagenfurt (Stadt)", "2", "201"),
    "hermagor": _d("Hermagor", "2", "203"),
    "klagenfurt-land": _d("Klagenfurt", "2", "204", "210"),
    "st-veit-glan": _d("St. Veit an der Glan", "2", "205", "210"),
    "spittal-drau": _d("Spittal an der Drau", "2", "206"),
    "villach": _d("Villach", "2", "202", "207"),
    "voelkermarkt": _d("Völkermarkt", "2", "208"),
    "wolfsberg": _d("Wolfsberg", "2", "209"),
    "eisenstadt-stadt": _d("Eisenstadt (Stadt)", "1", "101"),
    "rust": _d("Rust (Stadt)", "1", "102"),
    "eisenstadt-land": _d("Eisenstadt", "1", "103"),
    "guessing": _d("Güssing", "1", "104"),
    "jennersdorf": _d("Jennersdorf", "1", "105"),
    "mattersburg": _d("Mattersburg", "1", "106"),
    "neusiedl-see": _d("Neusiedl am See", "1", "107"),
    "oberpullendorf": _d("Oberpullendorf", "1", "108"),
    "oberwart": _d("Oberwart", "1", "109"),
}
CONSTITUENCIES_1927 = {
    1: ("wien-01", "wien-03", "wien-04"),
    2: ("wien-06", "wien-07", "wien-08"),
    3: ("wien-09", "wien-18", "wien-19"),
    4: ("wien-02", "wien-20", "wien-21"),
    5: ("wien-05", "wien-10", "wien-11"),
    6: ("wien-12", "wien-13", "wien-15"),
    7: ("wien-14", "wien-16", "wien-17"),
    13: ("braunau", "ried", "schaerding"),
    14: ("eferding", "grieskirchen", "voecklabruck", "wels"),
    17: ("salzburg-stadt", "hallein", "salzburg-umgebung", "st-johann-pongau", "tamsweg", "zell-am-see"),
    18: (
        "innsbruck-stadt", "imst", "innsbruck-land", "kitzbuehel", "kufstein", "landeck", "lienz", "reutte", "schwaz",
    ),
    19: ("bludenz", "bregenz", "feldkirch"),
    22: ("feldbach", "hartberg", "weiz"),
    23: ("bruck-mur", "groebming", "judenburg", "leoben", "liezen", "muerzzuschlag", "murau"),
    24: (
        "klagenfurt-stadt", "hermagor", "klagenfurt-land", "st-veit-glan", "spittal-drau", "villach", "voelkermarkt",
        "wolfsberg",
    ),
    25: (
        "eisenstadt-stadt", "rust", "eisenstadt-land", "guessing", "jennersdorf", "mattersburg", "neusiedl-see",
        "oberpullendorf", "oberwart",
    ),
}  # fmt: skip
"""The constituencies made up only of whole political districts, which the district rows must add up to."""
DISTRICT_NOTES = {
    1927: (
        "Lower Austria: the district rows, as read from the scan, add up to 498 more registered voters, 2 fewer "
        "Social Democratic votes, 3 fewer Landbund votes, and 5 more Communist votes than the sum the table prints "
        "for the rural districts (Landbezirke) and for the state. Every row agrees with itself (the lists add up to "
        "the valid votes, and the votes cast less the invalid votes are the valid votes), and the printed state "
        "totals agree with the constituency table, so the rows are stored as read and the state totals are declared "
        "as printed."
    ),
}


@dataclass
class DistrictTable:
    page: int
    columns: list[str]
    edges: list[tuple[float, float]]
    rows: list[Row] = field(default_factory=list)


@cache
def district_tables(year: int) -> tuple[list[DistrictTable], dict[str, dict[str, int | None]]]:
    """The district tables of a year and each district's figures, checked against every total the tables print."""
    found: list[DistrictTable] = []
    order: dict[tuple[str, ...], list[str]] = {}
    units: dict[str, dict[str, int | None]] = {}
    totals: list[tuple[str, tuple[str, ...], str, str, dict[str, int | None]]] = []
    differs: dict[tuple[str, str], int] = {}
    for line in (TABLES / f"{year}-bezirke.txt").read_text().splitlines():
        words = line.split("#", 1)[0].split()
        if not words:
            continue
        head, rest = words[0], words[1:]
        if head == "table":
            found.append(DistrictTable(int(rest[0].removeprefix("page=")), [], []))
        elif head == "columns":
            found[-1].columns = rest
        elif head == "x":
            edges = [float(x) for x in rest]
            found[-1].edges = list(zip(edges[::2], edges[1::2], strict=True))
        elif head == "row":
            y0, y1 = rest[1].removeprefix("@").split(":")
            values, splits = _cells(found[-1].columns, rest[2:])
            found[-1].rows.append(Row(rest[0], (float(y0), float(y1)), values, splits))
            order.setdefault(tuple(found[-1].columns), []).append(rest[0])
            units.setdefault(rest[0], {}).update(values)
        elif head == "total":
            first, last = rest[1].split("..")
            values = dict(zip(found[-1].columns, (_number(x) for x in rest[3:]), strict=True))
            totals.append((rest[0], tuple(found[-1].columns), first, last, values))
        elif head == "differs":
            differs[(rest[0], rest[1])] = int(rest[2])
        else:
            raise ValueError(f"{year}-bezirke.txt: unknown line {line!r}")
    if set(units) != set(DISTRICTS_1927):
        raise ValueError(f"{year}: districts {sorted(set(units) ^ set(DISTRICTS_1927))} are not both listed and read")
    for name, columns, first, last, printed in totals:
        codes = order[columns]
        span = codes[codes.index(first) : codes.index(last) + 1]
        for column in columns:
            summed = sum(units[code][column] or 0 for code in span)
            if summed - (printed[column] or 0) != differs.pop((name, column), 0):
                raise ValueError(f"{year} total {name}, {column}: rows add up to {summed}, printed {printed[column]}")
    if differs:
        raise ValueError(f"{year}: recorded differences {sorted(differs)} match no printed total")
    e = ELECTIONS[year]
    for code, values in units.items():
        _check(year, f"district {code}", values, list(e.columns))
    rows, _ = constituencies(year)
    for number, codes in CONSTITUENCIES_1927.items():
        for column, value in rows[str(number)].items():
            summed = sum(units[code].get(column) or 0 for code in codes)
            if summed != (value or 0):
                raise ValueError(f"{year} constituency {number}, {column}: districts add up to {summed}, not {value}")
    return found, units


METHOD_DISTRICTS = (
    "Transcribed from {table} of the Federal Ministry of the Interior's publication on this election (pages {pages} "
    "of {file}), which gives the results by political district, Vienna by municipal district (Gemeindebezirk), from "
    "the returns of each polling place. Every number was checked against the totals the table prints: the lists "
    "against the valid votes, the votes cast less the invalid votes against the valid votes, and the districts "
    "against the printed sums of each state's statutory cities and rural districts and the state totals; the "
    "constituencies made up of whole districts were checked against the constituency table. The state totals equal "
    "those of the constituency table, so the national totals are the same. A district of 1927 is linked, as an "
    "approximation, to the present political districts that cover most of its territory; the boundaries have "
    "changed since (Vienna gained its 22nd and 23rd districts, and Lower Austria, Upper Austria, Styria, "
    "Carinthia, and Vorarlberg have districts formed later). The set supersedes the one by constituency."
)


def districts(
    p: Package,
    e: Election,
    election_id: str,
    contest: str,
    below: str,
    options: dict[str, str],
    won: dict[str, int],
    national: dict[str, int | None],
    page_ids: dict[int, str],
    stored: raw.Artifact,
) -> None:
    """Add the result set by political district, which supersedes the one by constituency and takes the default."""
    from ea.sources.at import bmi

    found, _ = district_tables(e.year)
    result_set = key("result_set", e.year, "bezirke")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage=e.stage,
        kind="counted",
        source_id=BMI_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
        supersedes_id=below,
    )
    file = FILES[e.year].rsplit("/", 1)[1]
    pages = sorted({table.page for table in found})
    method = METHOD_DISTRICTS.format(table=DISTRICT_PAGES[e.year], pages=f"{pages[0]} to {pages[-1]}", file=file)
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    for index, text in enumerate((*e.notes, DISTRICT_NOTES[e.year])):
        p.add("note", id=key("note", result_set, index), entity_id=result_set, kind="caveat", body_md=text, lang="en")
    for column, n in won.items():
        p.add("outcome", result_set_id=result_set, option_id=options[column], kind="won", seats=n)
    for column, value in national.items():
        if value is None or (column not in MEASURES and column not in e.columns):
            continue
        option = None if column in MEASURES else options[column]
        measure = MEASURES.get(column, VOTES)
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    p.accept(
        "accounting.declared",
        "the Lower Austrian district rows of Tabelle 13 do not add up to the state totals it prints (498 registered "
        "voters, 2 Social Democratic, 3 Landbund, and 5 Communist votes); the printed totals are declared",
    )
    sizes = _pages(stored)
    for number in pages:
        if number in page_ids:
            continue
        width, height = sizes[number]
        page_ids[number] = key("page", file, number)
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact_id(FILES[e.year]),
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}/{number}.png",
            width=width,
            height=height,
        )
    shared: dict[str, int] = {}
    for spec in DISTRICTS_1927.values():
        for code in spec.successors:
            shared[code] = shared.get(code, 0) + 1
    unit_ids: dict[str, str] = {}
    for code, spec in DISTRICTS_1927.items():
        unit = unit_ids[code] = key("unit", e.year, "bezirk", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code="at.bezirk",
            code=f"{e.day}:{code}",
            area_relation="within",
            area_id=bmi.area("at.land", spec.land),
            kind="standard",
        )
        p.name(unit, spec.name, "de", "official")
        for successor in spec.successors:
            p.add(
                "reporting_unit_area",
                unit_id=unit,
                area_id=bmi.area("at.bezirk", successor),
                valid_from=e.day,
                valid_to=e.day + timedelta(days=1),
                approximate=True,
                partial=shared[successor] > 1,
                method="official_list",
            )
    for table in found:
        for row in table.rows:
            for column, (x0, x1) in zip(table.columns, table.edges, strict=True):
                value = row.values[column]
                if value is None:
                    continue
                option_id, measure = (None, MEASURES[column]) if column in MEASURES else (options[column], VOTES)
                _value(p, result_set, unit_ids[row.code], option_id, measure, value)
                p.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit_ids[row.code],
                    option_id=option_id,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    page_id=page_ids[table.page],
                    source_id=None,
                    x0=x0,
                    y0=row.band[0],
                    x1=x1,
                    y1=row.band[1],
                )
