import csv
import io
import re
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.de.bundeswahlleiterin import LAENDER
from ea.store import DATA, connect

SOURCE = "de.btw"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BWL = "https://www.bundeswahlleiterin.de"
RESULTS = f"{BWL}/dam/jcr/ce2d2b6a-f211-4355-8eea-355c98cd4e47/btw_kerg.zip"
HEFT = f"{BWL}/dam/jcr/397735e3-0585-46f6-a0b5-2c60c5b83de6/btw_ab49_gesamt.pdf"
ELECTIONS = {
    1949: (date(1949, 8, 14), date(1949, 9, 7)),
    1953: (date(1953, 9, 6), date(1953, 10, 6)),
    1957: (date(1957, 9, 15), date(1957, 10, 15)),
    1961: (date(1961, 9, 17), date(1961, 10, 17)),
    1965: (date(1965, 9, 19), date(1965, 10, 19)),
    1969: (date(1969, 9, 28), date(1969, 10, 20)),
    1972: (date(1972, 11, 19), date(1972, 12, 13)),
    1976: (date(1976, 10, 3), date(1976, 12, 14)),
    1980: (date(1980, 10, 5), date(1980, 11, 4)),
    1983: (date(1983, 3, 6), date(1983, 3, 29)),
    1987: (date(1987, 1, 25), date(1987, 2, 18)),
    1990: (date(1990, 12, 2), date(1990, 12, 20)),
    1994: (date(1994, 10, 16), date(1994, 11, 10)),
    1998: (date(1998, 9, 27), date(1998, 10, 26)),
    2002: (date(2002, 9, 22), date(2002, 10, 17)),
    2005: (date(2005, 9, 18), date(2005, 10, 18)),
    2009: (date(2009, 9, 27), date(2009, 10, 27)),
    2013: (date(2013, 9, 22), date(2013, 10, 22)),
    2017: (date(2017, 9, 24), date(2017, 10, 24)),
    2021: (date(2021, 9, 26), date(2021, 10, 26)),
}
NEXT_ELECTION = date(2025, 2, 23)
LONG = {2017: {"final": "btw2017_kerg2.csv"}, 2021: {"final": "btw2021-w_kerg2.csv", "original": "btw2021_kerg2.csv"}}
ACCOUNTING = {
    "Wahlberechtigte": 2,
    "Wähler": 14,
    "Wählende": 14,
    "Ungültige": 5,
    "Ungültige Stimmen": 5,
    "Gültige": 4,
    "Gültige Stimmen": 4,
}
OTHERS = {"Übrige", "UEBRIGE", "Einzelbewerber"}
INDEPENDENTS = "Parteilose"
SPELLINGS = {
    "GRUENE": "GRÜNE",
    "OEDP": "ÖDP",
    "BUERGERP": "Bürgerpartei",
    "MUENDIGE": "Mündige Bürger",
    "SOLIDARITAET": "SOLIDARITÄT",
    "HUMANWIRTSCHAFTS- PARTEI": "HUMANWIRTSCHAFTSPARTEI",
    "HEIMAT (2021: NPD)": "NPD",
    "Verjüngungsforschung (2021: Gesundheitsforschung)": "Gesundheitsforschung",
    "Wir Bürger (2021: LKR)": "LKR",
    "FP Deutschl.": "FP Deutschlands",
}
ALIASES = {
    "F.D.P.": "FDP",
    "GRUENE": "GRÜNE",
    "OEDP": "ÖDP",
    "ödp": "ÖDP",
    "BUERGERP": "Bürgerpartei",
    "MUENDIGE": "Mündige Bürger",
    "Mündige": "Mündige Bürger",
    "SOLIDARITAET": "ÖKO-UNION",
    "ÖKO-Union": "ÖKO-UNION",
    "Zentrum": "ZENTRUM",
    "PDS": "Die Linke",
    "Die Linke.": "Die Linke",
    "DIE LINKE": "Die Linke",
    "NPD": "HEIMAT",
    "HEIMAT (2021: NPD)": "HEIMAT",
    "Tierschutz": "Tierschutzpartei",
    "Die Tierschutzpartei": "Tierschutzpartei",
    "Gesundheitsforschung": "Verjüngungsforschung",
    "Verjüngungsforschung (2021: Gesundheitsforschung)": "Verjüngungsforschung",
    "Wir Bürger (2021: LKR)": "LKR",
    "Die Humanisten": "PdH",
    "B": "B*",
    "RRP": "Bündnis 21/RRP",
    "BSA": "SGP",
    "PSG": "SGP",
    "Patrioten": "BüSo",
    "PATRIOTEN": "BüSo",
    "Deutschland": "Volksabstimmung",
    "DIE GRAUEN": "GRAUE",
    "FSU": "HUMANWIRTSCHAFTSPARTEI",
    "HUMANWIRTSCHAFTS- PARTEI": "HUMANWIRTSCHAFTSPARTEI",
    "Schill": "Offensive D",
    "PARTEI DER VERNUNFT": "PDV",
    "MG": "Gartenpartei",
    "Frauen": "FRAUEN",
    "Violetten": "DIE VIOLETTEN",
    "FP Deutschl.": "FP Deutschlands",
    "BFB": "BFB – Die Offensive",
    "EP": "EFP",
    "Familie": "FAMILIE",
}
KPD = {
    1949: "KPD",
    1953: "KPD",
    1976: "KPD/AO",
    1983: "KPD/ML",
    1990: "KPD (Ost)",
    1994: "KPD (Ost)",
    2002: "KPD (Ost)",
}
KPD_ROWS = {"1949 und 1953": "KPD", "1976": "KPD/AO", "1983": "KPD/ML", "1990, 1994 und 2002": "KPD (Ost)"}
COMBINED_INDEPENDENTS = {(1949, "169"): "CDU"}
FULL_NAMES = {
    "B*": "bergpartei, die überpartei - ökoanarchistisch-realdadaistisches sammelbecken",
    "LfK": "» Partei für Kinder, Jugendliche und Familien « – Lobbyisten für Kinder –",
}
PARLGOV = {"DP": "912", "GB/BHE": "1507", "ZENTRUM": "137"}
TABLE_LAENDER = ["08", "09", "11", "12", "04", "02", "06", "13", "03", "05", "07", "10", "14", "15", "01", "16"]

RESULTS_NOTE = (
    "Votes by constituency for every Bundestag election from 1949 to 2021. Candidates are not named. Files show each "
    "party's votes and combine votes for independents and other candidates as Übrige (others), except in 2017 and "
    "2021, which list independents by surname."
)
SEATS_NOTE = (
    "Seats by party and Land are taken from table 10. Constituency winners are identified from the first-vote count "
    "and checked against the constituency seats declared in that table."
)
SINGLE_VOTE = (
    "In 1949, voters cast a single vote for both the constituency candidate and the party's Land list. Each vote "
    "appears twice: under the constituency contest and under the Land list."
)
BERLIN_WEST = (
    "Berlin-West took no part in the vote. Its members of the Bundestag (19 by 1952 and 22 from 1953) were chosen by "
    "the Berlin House of Representatives and are not included here."
)
REPEAT_2021 = (
    "On 11 February 2024, the election was repeated in parts of Berlin due to irregularities on election day. The "
    "results shown include the repeat vote and the resulting seats. The original 2021 count is kept as superseded "
    "results for the contests the repeat vote changed."
)
COMBINED = (
    "The source combines the votes of all independent candidates in this constituency. Together they outpolled the "
    "CDU candidate, who won the seat."
)
THRESHOLD_1990 = (
    "In 1990, the 5 percent threshold applied separately in the former West Germany (including Berlin-West) and the "
    "former East Germany (including Berlin-East). A party that passed it in either area took part in the seat "
    "allocation."
)
LISTS = {"order": "closed", "constituencies": "all", "exhausted": "vacant", "vacancy": "next_on_list"}
SYSTEMS = {
    1949: (
        "bundestag-1949",
        {
            "formula": "dhondt",
            "threshold": {"percent": 5, "level": "party", "scope": "contest", "constituencies": 1},
            "lists": LISTS,
        },
        "Bundestag seat allocation by Land, 1949",
        (
            "Each Land's seats were divided among parties by the D'Hondt method. Only parties that won at least 5 percent "
            "of the votes in that Land, or a constituency there, took part. Constituency winners kept their seats even "
            "when they exceeded the party's share (overhang seats)."
        ),
    ),
    1953: (
        "bundestag-1953",
        {
            "formula": "dhondt",
            "threshold": {"percent": 5, "level": "party", "scope": "national", "constituencies": 1},
            "lists": LISTS,
        },
        "Bundestag second-vote allocation, 1953",
        (
            "Seats were divided nationally by the D'Hondt method on second votes among parties that won at least 5 "
            "percent nationwide or one constituency, and then among each party's Land lists. Parties of national "
            "minorities were exempt from the threshold. Constituency winners kept their seats even when they exceeded the "
            "party's share (overhang seats)."
        ),
    ),
    1957: (
        "bundestag-1957",
        {
            "formula": "dhondt",
            "threshold": {"percent": 5, "level": "party", "scope": "national", "constituencies": 3},
            "lists": LISTS,
        },
        "Bundestag second-vote allocation, 1957 to 1983",
        (
            "Seats were divided nationally by the D'Hondt method on second votes among parties that won at least 5 "
            "percent nationwide or three constituencies, and then among each party's Land lists. Parties of national "
            "minorities were exempt from the threshold. Constituency winners kept their seats even when they exceeded the "
            "party's share (overhang seats)."
        ),
    ),
    1987: (
        "bundestag-1987",
        {
            "formula": "hare",
            "threshold": {"percent": 5, "level": "party", "scope": "national", "constituencies": 3},
            "lists": LISTS,
        },
        "Bundestag second-vote allocation, 1987 to 2005",
        (
            "Seats were divided nationally by the Hare–Niemeyer largest remainder method on second votes among parties "
            "that won at least 5 percent nationwide or three constituencies, and then among each party's Land lists. "
            "Parties of national minorities were exempt from the threshold. Constituency winners kept their seats even "
            "when they exceeded the party's share (overhang seats)."
        ),
    ),
    2009: (
        "bundestag-2009",
        {
            "formula": "sainte_lague_schepers",
            "threshold": {"percent": 5, "level": "party", "scope": "national", "constituencies": 3},
            "lists": LISTS,
        },
        "Bundestag second-vote allocation, 2009",
        (
            "Seats were divided nationally by the Sainte-Laguë/Schepers method on second votes among parties that won at "
            "least 5 percent nationwide or three constituencies, and then among each party's Land lists. Parties of "
            "national minorities were exempt from the threshold. Constituency winners kept their seats even when they "
            "exceeded the party's share (overhang seats)."
        ),
    ),
    2013: (
        "bundestag-2013",
        {
            "formula": "sainte_lague_schepers",
            "threshold": {"percent": 5, "level": "party", "scope": "national", "constituencies": 3},
            "lists": LISTS,
        },
        "Bundestag second-vote allocation with balance seats, 2013 to 2021",
        (
            "Seats were first divided among the Länder and then among the parties within each Land by the "
            "Sainte-Laguë/Schepers method, among parties that won at least 5 percent nationwide or three constituencies. "
            "The Bundestag was enlarged until each party's seats matched its national share of second votes to compensate "
            "for overhang seats. From 2021, up to three overhang seats went uncompensated."
        ),
    ),
}
CONSTITUENCIES = (
    "bundestag-constituencies-1949",
    "Bundestag constituencies, 1949 to 2021",
    (
        "The candidate with the most votes (the most first votes from 1953) won the constituency and kept the seat even "
        "when it exceeded the party's share."
    ),
)


def fold(text: str) -> str:
    text = text.upper().replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE").replace("ß", "SS")
    return re.sub(r"[\s\-–]+", " ", text).strip()


def fetch() -> None:
    raw.fetch(SOURCE, RESULTS, suffix=".zip")
    raw.fetch(SOURCE, HEFT, suffix=".pdf")


def parse() -> None:
    land_codes = {fold(name): code for code, (_, name, _) in LAENDER.items()}
    abbreviations = {abbreviation: code for code, (abbreviation, _, _) in LAENDER.items()}
    records: list[dict[str, object]] = []
    names: list[dict[str, object]] = []
    with zipfile.ZipFile(raw.cached(SOURCE, RESULTS).path) as archive:
        for year in ELECTIONS:
            if year in LONG:
                for version, member in LONG[year].items():
                    frame = pl.read_csv(
                        io.BytesIO(archive.read(member)), separator=";", skip_rows=9, infer_schema=False
                    ).filter(pl.col("Anzahl").is_not_null() & (pl.col("Gruppenname") != "Übrige"))
                    for row in frame.iter_rows(named=True):
                        area = row["Gebietsart"]
                        records.append(
                            {
                                "year": year,
                                "version": version,
                                "level": {"Wahlkreis": "wk", "Land": "land", "Bund": "bund"}[area],
                                "wk": row["Gebietsnummer"].zfill(3) if area == "Wahlkreis" else None,
                                "land": row["UegGebietsnummer"].zfill(2)
                                if area == "Wahlkreis"
                                else row["Gebietsnummer"].zfill(2)
                                if area == "Land"
                                else None,
                                "label": row["Gruppenname"],
                                "kind": "accounting"
                                if row["Gruppenart"] == "System-Gruppe"
                                else "independent"
                                if row["Gruppenart"] == "Einzelbewerber/Wählergruppe"
                                else "party",
                                "ballot": int(row["Stimme"] or 0),
                                "value": int(row["Anzahl"]),
                            }
                        )
                        if area == "Wahlkreis":
                            names.append(
                                {
                                    "year": year,
                                    "wk": row["Gebietsnummer"].zfill(3),
                                    "land": row["UegGebietsnummer"].zfill(2),
                                    "name": row["Gebietsname"],
                                }
                            )
                continue
            content = archive.read(f"btw{year}_kerg.csv")
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = content.decode("cp1252")
            rows = list(csv.reader(text.splitlines(), delimiter=";"))
            start = next(i for i, row in enumerate(rows) if row and row[0] in ("Wahlkreis", "Nr"))
            header, ballots = rows[start], rows[start + 1]
            periods = rows[start + 2] if "Endgültig" in rows[start + 2] else None
            columns: list[tuple[int, str, int]] = []
            label = ballot_label = ""
            for j in range(3, len(header)):
                if periods is not None:
                    label = header[j] or label
                    ballot_label = ballots[j] or ballot_label
                    if periods[j] != "Endgültig":
                        continue
                else:
                    label, ballot_label = header[j], ballots[j]
                if not label:
                    continue
                columns.append((j, label, {"Erststimmen": 1, "Zweitstimmen": 2}.get(ballot_label, 0)))
            data = [row for row in rows[start + (3 if periods else 2) :] if row and row[0].strip().isdigit()]
            lands = {row[0].strip(): land_codes[fold(row[1])] for row in data if 900 < int(row[0]) < 999}
            for row in data:
                number = int(row[0])
                if number == 999:
                    level, wk, land = "bund", None, None
                elif number > 900:
                    level, wk, land = "land", None, lands[row[0].strip()]
                else:
                    owner = row[2].strip()
                    level, wk = "wk", f"{number:03d}"
                    land = (
                        abbreviations[owner]
                        if owner in abbreviations
                        else lands[str(int(owner) + (0 if int(owner) > 900 else 900))]
                    )
                    names.append({"year": year, "wk": wk, "land": land, "name": row[1].strip()})
                for j, label, ballot in columns:
                    cell = row[j].strip() if j < len(row) else ""
                    if not cell:
                        continue
                    if label in ACCOUNTING:
                        kind = "accounting"
                        if ballot == 2 and ACCOUNTING[label] in (2, 14):
                            continue
                        if ACCOUNTING[label] in (2, 14):
                            ballot = 0
                    elif label in OTHERS:
                        kind, label = "others", "Übrige"
                    elif label == INDEPENDENTS:
                        kind = "independent"
                    else:
                        kind = "party"
                    for each in (
                        (1, 2) if year == 1949 and ballot == 0 and ACCOUNTING.get(label) not in (2, 14) else (ballot,)
                    ):
                        records.append(
                            {
                                "year": year,
                                "version": "final",
                                "level": level,
                                "wk": wk,
                                "land": land,
                                "label": label,
                                "kind": kind,
                                "ballot": each,
                                "value": int(cell),
                            }
                        )
    values = pl.DataFrame(records).with_columns(
        label=pl.when(pl.col("label").str.contains(r"^(\S{1,2} )+\S{1,2}$"))
        .then(pl.col("label").str.replace_all(" ", ""))
        .otherwise(pl.col("label"))
    )
    values = values.with_columns(
        measure=pl.when(pl.col("kind") == "accounting")
        .then(pl.col("label").replace_strict(ACCOUNTING, default=None))
        .otherwise(pl.lit(1))
        .cast(pl.Int16),
        ballot=pl.col("ballot").cast(pl.Int16),
    )
    unknown = values.filter((pl.col("kind") == "accounting") & pl.col("measure").is_null())
    if unknown.height:
        raise ValueError(f"unknown accounting rows: {unknown['label'].unique().to_list()}")
    values = values.with_columns(
        group=pl.when(pl.col("kind") == "party")
        .then(
            pl.when(pl.col("label") == "KPD")
            .then(pl.col("year").replace_strict(KPD, default=None))
            .otherwise(pl.col("label").replace(ALIASES))
        )
        .when(pl.col("kind") == "accounting")
        .then(None)
        .otherwise(pl.col("label"))
    )
    listed_independents = (pl.col("ballot") == 2) & (pl.col("kind") == "independent")
    values = values.with_columns(
        kind=pl.when(listed_independents).then(pl.lit("others")).otherwise(pl.col("kind")),
        group=pl.when(listed_independents).then(pl.lit("Übrige")).otherwise(pl.col("group")),
    )
    ranked = (
        values.filter((pl.col("kind") == "independent") & (pl.col("level") == "wk"))
        .with_columns(rank=pl.col("value").rank("ordinal", descending=True).over("year", "version", "wk", "ballot"))
        .select("year", "version", "wk", "ballot", "rank", "group")
    )
    renamed = (
        ranked.filter(pl.col("version") == "original")
        .join(
            ranked.filter(pl.col("version") == "final").drop("version"),
            on=["year", "wk", "ballot", "rank"],
            suffix="_final",
        )
        .select("year", "version", "wk", "ballot", "group", "group_final")
    )
    if ranked.filter(pl.col("version") == "original").height != renamed.height:
        raise ValueError("2021 independents without a counterpart after the repeat vote")
    values = (
        values.join(renamed, on=["year", "version", "wk", "ballot", "group"], how="left", nulls_equal=True)
        .with_columns(group=pl.coalesce("group_final", "group"), label=pl.coalesce("group_final", "label"))
        .drop("group_final")
    )
    values = values.with_columns(
        display=pl.when(pl.col("kind") == "party")
        .then(pl.col("label").replace(SPELLINGS))
        .otherwise(pl.col("group").str.strip_prefix("EB:").str.strip_chars())
    )
    if values.filter((pl.col("label") == "KPD") & pl.col("group").is_null()).height:
        raise ValueError("a KPD outside the known KPD elections")

    keys = ["year", "version", "ballot", "measure", "kind", "group"]
    for label, coarse, fine, by in (("Land", "land", "wk", ["land"]), ("Bund", "bund", "land", [])):
        sums = (
            values.filter((pl.col("level") == fine) & pl.col("kind").is_in(["party", "accounting"]))
            .group_by(*by, *keys)
            .agg(pl.col("value").sum().alias("sum"))
        )
        short = (
            values.filter((pl.col("level") == coarse) & pl.col("kind").is_in(["party", "accounting"]))
            .select(*by, *keys, "value")
            .join(sums, on=[*by, *keys], how="full", coalesce=True, nulls_equal=True)
            .filter(pl.col("value").fill_null(0) != pl.col("sum").fill_null(0))
        )
        if short.height:
            raise ValueError(f"{label} totals differ from the sum of their parts: {short.head(10)}")
    values = values.filter(pl.col("level") == "wk").drop("level")
    accounting = values.filter(pl.col("kind") == "accounting")
    valid = (
        values.filter(pl.col("kind") != "accounting")
        .group_by("year", "version", "wk", "ballot")
        .agg(pl.col("value").sum().alias("votes"))
        .join(
            accounting.filter(pl.col("measure") == 4).select("year", "version", "wk", "ballot", valid="value"),
            on=["year", "version", "wk", "ballot"],
            how="full",
            coalesce=True,
        )
        .filter(pl.col("votes").ne_missing(pl.col("valid")))
    )
    if valid.height:
        raise ValueError(f"option votes do not add up to the valid votes: {valid.head(10)}")
    values = values.filter((pl.col("kind") == "accounting") | (pl.col("value") > 0))

    constituencies = pl.DataFrame(names).unique(["year", "wk"], keep="first").sort("year", "wk")
    proper = {fold(name): name for name in constituencies["name"] if not name.isupper()}
    readable: list[str] = []
    for name in constituencies["name"]:
        if not name.isupper():
            readable.append(name)
        elif fold(name) in proper:
            readable.append(proper[fold(name)])
        else:
            readable.append(
                re.sub(
                    r"\b(Und|An|Am|Im|In|Der|Die|Ob|Bei|Vor|Auf)\b", lambda match: match.group(0).lower(), name.title()
                )
                .replace("ae", "ä")
                .replace("oe", "ö")
                .replace("ue", "ü")
                .replace("Ue", "Ü")
            )
    constituencies = constituencies.with_columns(name=pl.Series(readable))

    winners = (
        values.filter((pl.col("ballot") == 1) & pl.col("kind").is_in(["party", "independent", "others"]))
        .with_columns(
            top=pl.col("value") == pl.col("value").max().over("year", "version", "wk"),
            declared=pl.struct("year", "wk").map_elements(
                lambda key: COMBINED_INDEPENDENTS.get((key["year"], key["wk"])), return_dtype=pl.String
            ),
        )
        .filter(
            pl.when(pl.col("declared").is_null()).then(pl.col("top")).otherwise(pl.col("group") == pl.col("declared"))
        )
    )
    problems = {
        "ties for first place": winners.filter(pl.len().over("year", "version", "wk") > 1),
        "constituencies topped by combined others": winners.filter(pl.col("kind") == "others"),
    }
    failures = [f"{label}: {frame.head(5)}" for label, frame in problems.items() if frame.height]
    if failures:
        raise ValueError("\n".join(failures))
    winners = winners.select("year", "version", "wk", "land", "group")

    pdf = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, HEFT).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    seats: list[dict[str, object]] = []
    section = party = None
    for line in pdf.splitlines():
        stripped = line.strip()
        heading = re.sub(r"\d+$", "", stripped.removeprefix("noch ")).strip()
        if heading in ("Wahlkreissitze", "Landeslistensitze", "Wahlkreis- und Landeslistensitze"):
            section = heading
            continue
        if stripped.startswith("10 Sitze der Parteien") and "...." not in stripped:
            continue
        if stripped.startswith("11 Vom Berliner") and "...." not in stripped:
            break
        if section is None:
            continue
        match = re.match(r"^\s{1,3}(\d{4})\d?\s+(.*)$", line)
        if match is None:
            if re.match(r"^\s{8,12}\S", line) and not re.search(r"\d{2,}", stripped) and len(stripped) < 40:
                party = heading
            continue
        tokens = re.findall(r"\d+(?: \(\d+\))?|–|X", match.group(2))
        if len(tokens) != 17:
            raise ValueError(f"table 10 row without 17 cells: {line}")
        for land, token in zip(["DE", *TABLE_LAENDER], tokens, strict=True):
            number = re.match(r"\d+", token)
            seats.append(
                {
                    "section": section,
                    "party": party,
                    "year": int(match.group(1)),
                    "land": land,
                    "seats": None if number is None else int(number.group(0)),
                    "blocked": token == "X",
                }
            )
    keys = ["section", "party", "year"]
    table = pl.DataFrame(seats, infer_schema_length=None)
    table = table.join(
        table.group_by(keys).agg(
            surplus=pl.col("seats").filter(pl.col("land") != "DE").sum()
            - pl.col("seats").filter(pl.col("land") == "DE").sum()
        ),
        on=keys,
    ).with_columns(
        glued=(pl.col("surplus") != 0)
        & (pl.col("land") != "DE")
        & (pl.col("seats") >= 10)
        & (pl.col("seats") - pl.col("seats") // 10 == pl.col("surplus"))
    )
    unexplained = (
        table.group_by(keys)
        .agg(pl.col("surplus").first(), pl.col("glued").sum())
        .filter((pl.col("surplus") != 0) & (pl.col("glued") != 1))
    )
    if unexplained.height:
        raise ValueError(f"table 10 Länder do not add up: {unexplained}")
    table = (
        table.with_columns(seats=pl.when(pl.col("glued")).then(pl.col("seats") // 10).otherwise(pl.col("seats")))
        .filter(pl.col("year").is_in(list(ELECTIONS)) & (pl.col("land") != "DE") & ~pl.col("blocked"))
        .with_columns(
            group=pl.when(pl.col("party") == "CDU und CSU")
            .then(pl.when(pl.col("land") == "09").then(pl.lit("CSU")).otherwise(pl.lit("CDU")))
            .when((pl.col("party") == "GRÜNE") & (pl.col("year") == 1990))
            .then(pl.lit("B90/Gr"))
            .otherwise(pl.col("party")),
            section=pl.col("section").replace_strict(
                {
                    "Wahlkreissitze": "constituency",
                    "Landeslistensitze": "list",
                    "Wahlkreis- und Landeslistensitze": "total",
                }
            ),
        )
    )
    totals = table.filter(pl.col("party").is_in(["Zusammen", "Insgesamt"])).select("year", "land", "section", "seats")
    table = (
        table.filter(~pl.col("party").is_in(["Zusammen", "Insgesamt"]))
        .pivot("section", index=["year", "land", "group"], values="seats")
        .with_columns(pl.col("constituency", "list", "total").fill_null(0))
        .filter(pl.col("total") > 0)
    )
    summed = table.group_by("year", "land").agg(pl.col("constituency", "total").sum())
    problems = {
        "parties whose seats are not constituency plus list seats": table.filter(
            pl.col("constituency") + pl.col("list") != pl.col("total")
        ),
        "Länder whose party seats miss the table totals": summed.join(
            totals.pivot("section", index=["year", "land"], values="seats"), on=["year", "land"], suffix="_table"
        ).filter((pl.col("constituency") != pl.col("constituency_table")) | (pl.col("total") != pl.col("total_table"))),
        "constituency winners that differ from the declared constituency seats": winners.filter(
            pl.col("version") == "final"
        )
        .group_by("year", "land", "group")
        .len("won")
        .join(table.filter(pl.col("constituency") > 0), on=["year", "land", "group"], how="full", coalesce=True)
        .filter(pl.col("won").fill_null(0) != pl.col("constituency").fill_null(0)),
    }
    failures = [f"{label}: {frame.head(10)}" for label, frame in problems.items() if frame.height]
    if failures:
        raise ValueError("\n".join(failures))
    entries: list[list[str]] = []
    inside = False
    for line in pdf.splitlines():
        if "Bundestagswahlen seit 1949" in line and line.strip().endswith("Bundestagswahlen seit 1949"):
            inside = True
            continue
        if (
            not inside
            or not line.strip()
            or line.lstrip().startswith(("1 Wenn", "2 Bundestagswahl", "Informationen der"))
        ):
            if line.lstrip().startswith("Informationen der"):
                inside = False
            continue
        short, full, held = line[:31].strip(), line[31:110].strip(), line[110:143].strip()
        if line[:1] != " " and short:
            entries.append([short, full, held])
        elif entries:
            if short:
                previous = entries[-1][0]
                entries[-1][0] = (
                    previous[:-1] + short
                    if previous.endswith("-")
                    else previous + short
                    if previous.endswith("/")
                    else f"{previous} {short}"
                )
            if full:
                previous = entries[-1][1]
                entries[-1][1] = (
                    (previous[:-1] if full[0].islower() else previous) + full
                    if previous.endswith("-")
                    else f"{previous} {full}"
                )
            if held:
                entries[-1][2] = f"{entries[-1][2]} {held}"
    full_names = {KPD_ROWS[held]: full for short, full, held in entries if short == "KPD"}
    for split in (False, True):
        for short, full, _ in entries:
            if short != "KPD":
                for alternative in [*short.split("/"), short.split(" (")[0]] if split else [short, short.rstrip(".")]:
                    full_names.setdefault(alternative, full)
    full_names |= FULL_NAMES
    full_names["HEIMAT"] = full_names["NPD"]
    groups = set(values.filter(pl.col("kind") == "party")["group"]) | set(table["group"])
    groups -= {INDEPENDENTS}
    missing = sorted(group for group in groups if group not in full_names)
    if missing:
        raise ValueError(f"parties missing from the appendix: {missing}")
    parties = pl.DataFrame(
        [(group, full_names[group]) for group in sorted(groups)], schema=["group", "name"], orient="row"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    parties.write_parquet(PARSED / "parties.parquet")
    values.write_parquet(PARSED / "values.parquet")
    constituencies.write_parquet(PARSED / "constituencies.parquet")
    winners.write_parquet(PARSED / "winners.parquet")
    table.write_parquet(PARSED / "seats.parquet")


def emit() -> Path:
    values = pl.read_parquet(PARSED / "values.parquet")
    constituencies = pl.read_parquet(PARSED / "constituencies.parquet")
    winners = pl.read_parquet(PARSED / "winners.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    parties = pl.read_parquet(PARSED / "parties.parquet")
    values = values.with_columns(
        set_key=pl.when(pl.col("ballot") == 0)
        .then(pl.lit("election"))
        .when(pl.col("ballot") == 1)
        .then(pl.col("wk"))
        .otherwise(pl.lit("list:") + pl.col("land"))
    )
    compared = ["set_key", "wk", "ballot", "measure", "group", "value"]
    final_2021 = values.filter((pl.col("year") == 2021) & (pl.col("version") == "final"))
    original_2021 = values.filter((pl.col("year") == 2021) & (pl.col("version") == "original"))
    changed = set(
        pl.concat(
            [
                final_2021.select(compared).join(
                    original_2021.select(compared), on=compared, how="anti", nulls_equal=True
                ),
                original_2021.select(compared).join(
                    final_2021.select(compared), on=compared, how="anti", nulls_equal=True
                ),
            ]
        )["set_key"]
    )
    values = values.filter((pl.col("version") == "final") | pl.col("set_key").is_in(list(changed)))
    years = list(ELECTIONS)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="German federal elections from 1949 to 2021 by constituency and Land",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "de", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        results = cs.source("Die Bundeswahlleiterin", "Bundestagswahlen 1949 bis 2021: Ergebnisse nach Wahlkreisen")
        heft = cs.source("Die Bundeswahlleiterin", "Ergebnisse früherer Bundestagswahlen")
        for source, title, url, notes in (
            (results, "Bundestagswahlen 1949 bis 2021: Ergebnisse nach Wahlkreisen", RESULTS, RESULTS_NOTE),
            (heft, "Ergebnisse früherer Bundestagswahlen", HEFT, SEATS_NOTE),
        ):
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Die Bundeswahlleiterin",
                title=title,
                url=url,
                license="Datenlizenz Deutschland – Namensnennung – Version 2.0",
                license_url="https://www.govdata.de/dl-de/by-2-0",
                attribution="© Die Bundeswahlleiterin, Wiesbaden 2025",
                notes=notes,
            )
            cs.artifact(raw.cached(SOURCE, url), source)

        germany = cs.polity("DE")
        nation = cs.area("de.nation", "DE")
        laender = {code: cs.area("de.land", code) for code in LAENDER}
        bundestag = cs.key("de.body", "bundestag")
        if {germany, nation, bundestag, *laender.values()} & cs.minted:
            raise ValueError("publish de.bundeswahlleiterin before de.btw")

        party_ids: dict[str, UUID] = {}
        for group, label in parties.iter_rows():
            if group in PARLGOV:
                party_ids[group] = cs.key("parlgov", PARLGOV[group])
                cs.add("external_id", entity_id=party_ids[group], scheme="de.party", value=group.upper())
                continue
            party_ids[group] = cs.key("de.party", group.upper())
            if party_ids[group] in cs.minted:
                cs.add("party", id=party_ids[group], kind="party", polity_id=germany)
                name(party_ids[group], label)
                if group != label:
                    name(party_ids[group], group, "de", "abbreviation")

        systems: dict[int, UUID] = {}
        for start, (key, rules, label, methodology) in SYSTEMS.items():
            systems[start] = cs.key("de.electoral_system", key)
            cs.add("electoral_system", id=systems[start], polity_id=germany, rules=rules, rules_version=1)
            name(systems[start], label, "en")
            cs.add("note", entity_id=systems[start], kind="methodology", body_md=methodology)
        constituency_system = cs.key("de.electoral_system", CONSTITUENCIES[0])
        cs.add(
            "electoral_system", id=constituency_system, polity_id=germany, rules={"formula": "fptp"}, rules_version=1
        )
        name(constituency_system, CONSTITUENCIES[1], "en")
        cs.add("note", entity_id=constituency_system, kind="methodology", body_md=CONSTITUENCIES[2])

        set_ids: dict[str, str] = {}
        unit_ids: dict[str, str] = {}
        option_ids: dict[str, str] = {}
        for index, year in enumerate(years):
            day, constituted = ELECTIONS[year]
            until = ELECTIONS[years[index + 1]][0] if index + 1 < len(years) else NEXT_ELECTION
            year_values = values.filter(pl.col("year") == year)
            year_seats = seats.filter(pl.col("year") == year)
            total = int(year_seats["total"].sum())
            if year != 2021:
                cs.add(
                    "body_size",
                    body_id=bundestag,
                    seats=total,
                    valid_from=constituted,
                    valid_to=ELECTIONS[years[index + 1]][1],
                )
            election = cs.key("de.election", str(day))
            cs.add(
                "election", id=election, polity_id=germany, kind="general", date_from=day, date_to=day, status="held"
            )
            name(election, f"{year} German federal election", "en")
            name(election, f"Bundestagswahl {year}", "de", "native")
            union = cs.key("de.alliance", f"{day}:union")
            cs.add("alliance", id=union, kind="joint_list", election_id=election)
            name(union, "CDU/CSU", "en")
            name(union, "CDU/CSU", "de", "native")
            cs.add("alliance_member", alliance_id=union, party_id=party_ids["CDU"], role=None)
            cs.add("alliance_member", alliance_id=union, party_id=party_ids["CSU"], role=None)
            path = f"de/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            if year == 1949:
                cs.add("note", entity_id=election, kind="caveat", body_md=SINGLE_VOTE)
            if year == 2021:
                cs.add("note", entity_id=election, kind="caveat", body_md=REPEAT_2021)

            areas: dict[str, UUID] = {}
            labels: dict[str, str] = {}
            year_constituencies = constituencies.filter(pl.col("year") == year)
            duplicated = set(year_constituencies.filter(pl.col("name").is_duplicated())["name"])
            for _, wk, land, label in year_constituencies.iter_rows():
                labels[wk] = label
                areas[wk] = cs.area("de.wahlkreis", wk, day)
                cs.add("area", id=areas[wk], scheme_code="de.wahlkreis")
                cs.add("area_code", area_id=areas[wk], code=wk, valid_from=day, valid_to=until)
                cs.add("area_parent", area_id=areas[wk], parent_id=laender[land], valid_from=day, valid_to=until)
                name(areas[wk], label)
                unit = cs.unit("de.wahlkreis", wk, day)
                unit_ids[f"{year}:{wk}"] = str(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=germany,
                    scheme_code="de.wahlkreis",
                    code=wk,
                    area_relation="equals",
                    area_id=areas[wk],
                    kind="standard",
                    valid_from=day,
                    valid_to=until,
                )
            cs.add(
                "boundary_set", scheme_code="de.wahlkreis", vintage=str(year), valid_from=day, valid_to=until, as_of=day
            )

            defaults: dict[str, UUID] = {}

            def result_set(
                key: str,
                contest: UUID | None,
                year: int = year,
                day: date = day,
                election: UUID = election,
                defaults: dict[str, UUID] = defaults,
            ) -> None:
                original = None
                if year == 2021 and key in changed:
                    original = cs.key(f"{SOURCE}.result_set", f"{day}:{key}:original")
                    cs.add(
                        "result_set",
                        id=original,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="superseded",
                        source_id=results,
                        is_default=False,
                        completeness=1,
                    )
                    if original in cs.minted:
                        set_ids[f"{year}:original:{key}"] = str(original)
                set_id = cs.key(f"{SOURCE}.result_set", f"{day}:{key}")
                cs.add(
                    "result_set",
                    id=set_id,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=heft if key == "national" else results,
                    is_default=True,
                    supersedes_id=original,
                    completeness=0 if key == "national" else 1,
                )
                defaults[key] = set_id
                if set_id in cs.minted:
                    set_ids[f"{year}:final:{key}"] = str(set_id)

            system = systems[max(start for start in SYSTEMS if start <= year)]
            national = cs.key("de.contest", f"{day}:bundestag")
            stood = set(year_values.filter(pl.col("kind") == "party")["group"])
            cs.add(
                "contest",
                id=national,
                election_id=election,
                kind="list_allocation",
                body_id=bundestag,
                district_area_id=nation,
                magnitude=total,
                electoral_system_id=system,
                rules_override={"threshold": {"exempt": [str(party_ids["SSW"])]}}
                if year >= 1953 and "SSW" in stood
                else None,
                default_result_kind="counted",
            )
            name(national, f"{year} German federal election, national seat allocation", "en")
            cs.add("slug", path=f"{path}/bundestag", entity_id=national)
            if year <= 1987:
                cs.add("note", entity_id=national, kind="caveat", body_md=BERLIN_WEST)
            if year == 1990:
                cs.add("note", entity_id=national, kind="caveat", body_md=THRESHOLD_1990)
            result_set("national", national)
            result_set("election", None)

            displays = dict(
                year_values.filter(pl.col("kind") != "accounting").select("group", "display").unique().iter_rows()
            )
            national_options: dict[str, UUID] = {}
            list_votes = year_values.filter((pl.col("ballot") == 2) & (pl.col("kind") != "accounting"))
            for land in sorted(set(year_constituencies["land"])):
                contest_key = f"{day}:list:{land}"
                contest = cs.key("de.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=bundestag,
                    district_area_id=laender[land],
                    allocation_parent_id=national,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} German federal election, {LAENDER[land][1]} party lists", "en")
                cs.add("slug", path=f"{path}/{slug(LAENDER[land][1])}-lists", entity_id=contest)
                result_set(f"list:{land}", contest)
                land_seats = dict(year_seats.filter(pl.col("land") == land).select("group", "total").iter_rows())
                groups = sorted(set(list_votes.filter(pl.col("land") == land)["group"]))
                missing = set(land_seats) - set(groups) - {INDEPENDENTS}
                if missing:
                    raise ValueError(f"{year} {land}: seats for parties without list votes: {missing}")
                for group in groups:
                    others = group == "Übrige"
                    option = cs.key("de.option", f"{contest_key}:{'others' if others else group}")
                    option_ids[f"{year}:2:{land}:{group}"] = str(option)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if others else "list",
                        party_id=None if others else party_ids[group],
                        alliance_id=union if group in ("CDU", "CSU") else None,
                        ballot_label=displays[group],
                    )
                    if others:
                        continue
                    if group not in national_options:
                        national_options[group] = cs.key("de.option", f"{day}:bundestag:{group}")
                        cs.add(
                            "contest_option",
                            id=national_options[group],
                            contest_id=national,
                            kind="list",
                            party_id=party_ids[group],
                            alliance_id=union if group in ("CDU", "CSU") else None,
                            ballot_label=displays[group],
                        )
                    if land_seats.get(group):
                        cs.add(
                            "outcome",
                            result_set_id=defaults[f"list:{land}"],
                            option_id=option,
                            kind="won",
                            seats=land_seats[group],
                        )
            for group, won in (
                year_seats.filter(pl.col("group") != INDEPENDENTS)
                .group_by("group")
                .agg(pl.col("total").sum())
                .iter_rows()
            ):
                cs.add(
                    "outcome",
                    result_set_id=defaults["national"],
                    option_id=national_options[group],
                    kind="won",
                    seats=won,
                )

            elected = {
                (version, wk): group
                for version, wk, group in winners.filter(pl.col("year") == year)
                .select("version", "wk", "group")
                .iter_rows()
            }
            candidates = (
                year_values.filter((pl.col("ballot") == 1) & (pl.col("kind") != "accounting"))
                .select("wk", "group", "kind")
                .unique()
                .sort("wk", "group")
            )
            for wk, frame in candidates.group_by("wk", maintain_order=True):
                wk = str(wk[0])
                contest_key = f"{day}:{wk}"
                contest = cs.key("de.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=bundestag,
                    district_area_id=areas[wk],
                    magnitude=1,
                    electoral_system_id=constituency_system,
                    default_result_kind="counted",
                )
                name(contest, f"Constituency {int(wk)}, {labels[wk]}", "en")
                cs.add(
                    "slug",
                    path=f"{path}/{slug(labels[wk])}{f'-{int(wk)}' if labels[wk] in duplicated else ''}",
                    entity_id=contest,
                )
                if (year, wk) in COMBINED_INDEPENDENTS:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=COMBINED)
                result_set(wk, contest)
                for group, kind in frame.select("group", "kind").iter_rows():
                    option = cs.key("de.option", f"{contest_key}:{'others' if kind == 'others' else group}")
                    option_ids[f"{year}:1:{wk}:{group}"] = str(option)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if kind == "others" else "candidate",
                        party_id=party_ids[group] if kind == "party" else None,
                        alliance_id=union if kind == "party" and group in ("CDU", "CSU") else None,
                        ballot_label=displays[group],
                    )
                    for version in ("final", "original"):
                        if elected.get((version, wk)) != group:
                            continue
                        target = defaults[wk] if version == "final" else None
                        if version == "original" and wk in changed:
                            target = cs.key(f"{SOURCE}.result_set", f"{day}:{wk}:original")
                        if target is not None:
                            cs.add("outcome", result_set_id=target, option_id=option, kind="elected")

        place = pl.when(pl.col("ballot") == 2).then(pl.col("land")).otherwise(pl.col("wk"))
        rows = values.with_columns(
            set_id=pl.format("{}:{}:{}", "year", "version", "set_key"),
            option=pl.when(pl.col("kind") == "accounting")
            .then(None)
            .otherwise(pl.format("{}:{}:{}:{}", "year", "ballot", place, "group")),
        ).filter(pl.col("set_id").is_in(list(set_ids)))
        cs.frame(
            "result_value",
            rows.select(
                result_set_id=pl.col("set_id").replace_strict(set_ids),
                unit_id=pl.format("{}:{}", "year", "wk").replace_strict(unit_ids),
                option_id=pl.col("option").replace_strict(option_ids, default=None),
                channel=pl.lit(0, pl.Int16),
                measure="measure",
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value="value",
            ),
        )
        return cs.write()
