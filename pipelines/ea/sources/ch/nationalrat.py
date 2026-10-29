import itertools
import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "ch.nationalrat"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PX = "https://www.pxweb.bfs.admin.ch/api/v1/de/px-x-1702020000_{0}/px-x-1702020000_{0}.px"
SEATS = "https://dam-api.bfs.admin.ch/hub/api/dam/assets/28765024/master"
OGD = "https://ogd-static.voteinfo-app.ch/v4/ogd/sd-t-17.02-NRW2023-{}.json"
PUBLISHER = "Federal Statistical Office"
DAYS = {
    1919: date(1919, 10, 26),
    1922: date(1922, 10, 29),
    1925: date(1925, 10, 25),
    1928: date(1928, 10, 28),
    1931: date(1931, 10, 25),
    1935: date(1935, 10, 27),
    1939: date(1939, 10, 29),
    1943: date(1943, 10, 31),
    1947: date(1947, 10, 26),
    1951: date(1951, 10, 28),
    1955: date(1955, 10, 30),
    1959: date(1959, 10, 25),
    1963: date(1963, 10, 27),
    1967: date(1967, 10, 29),
    1971: date(1971, 10, 31),
    1975: date(1975, 10, 26),
    1979: date(1979, 10, 21),
    1983: date(1983, 10, 23),
    1987: date(1987, 10, 18),
    1991: date(1991, 10, 20),
    1995: date(1995, 10, 22),
    1999: date(1999, 10, 24),
    2003: date(2003, 10, 19),
    2007: date(2007, 10, 21),
    2011: date(2011, 10, 23),
    2015: date(2015, 10, 18),
    2019: date(2019, 10, 20),
    2023: date(2023, 10, 22),
}
COUNTED = [year for year in DAYS if year >= 1971]
CANTONS = {
    "ZH": ("Zurich", "Zürich"),
    "BE": ("Bern", "Bern"),
    "LU": ("Lucerne", "Luzern"),
    "UR": ("Uri", "Uri"),
    "SZ": ("Schwyz", "Schwyz"),
    "OW": ("Obwalden", "Obwalden"),
    "NW": ("Nidwalden", "Nidwalden"),
    "GL": ("Glarus", "Glarus"),
    "ZG": ("Zug", "Zug"),
    "FR": ("Fribourg", "Freiburg"),
    "SO": ("Solothurn", "Solothurn"),
    "BS": ("Basel-Stadt", "Basel-Stadt"),
    "BL": ("Basel-Landschaft", "Basel-Landschaft"),
    "SH": ("Schaffhausen", "Schaffhausen"),
    "AR": ("Appenzell Ausserrhoden", "Appenzell A. Rh."),
    "AI": ("Appenzell Innerrhoden", "Appenzell I. Rh."),
    "SG": ("St. Gallen", "St. Gallen"),
    "GR": ("Graubünden", "Graubünden"),
    "AG": ("Aargau", "Aargau"),
    "TG": ("Thurgau", "Thurgau"),
    "TI": ("Ticino", "Tessin"),
    "VD": ("Vaud", "Waadt"),
    "VS": ("Valais", "Wallis"),
    "NE": ("Neuchâtel", "Neuenburg"),
    "GE": ("Geneva", "Genf"),
    "JU": ("Jura", "Jura"),
}
LANGUAGES = {"FR": "fr", "VD": "fr", "VS": "fr", "NE": "fr", "GE": "fr", "JU": "fr", "TI": "it"}
PX_PARTIES = {
    "1": "FDP",
    "34": "Mitte",
    "3": "SP",
    "4": "SVP",
    "5": "LPS",
    "7": "EVP",
    "8": "CSP",
    "31": "GLP",
    "9": "PdA",
    "27": "Sol",
    "12": "FGA",
    "13": "GP",
    "15": "SD",
    "16": "EDU",
    "18": "Lega",
    "33": "MCR",
    "2": "CVP",
    "32": "BDP",
    "6": "LdU",
    "11": "POCH",
    "14": "Rep",
    "10": "PSA",
    "17": "FPS",
    "26": "Sep",
    "35": "others",
}
SHEET_PARTIES = {
    "FDP": "FDP",
    "CVP": "CVP",
    "Die Mitte": "Mitte",
    "SP": "SP",
    "SVP": "SVP",
    "Dem.": "Dem",
    "LPS": "LPS",
    "LdU": "LdU",
    "EVP": "EVP",
    "CSP": "CSP",
    "GLP": "GLP",
    "BDP": "BDP",
    "PdA": "PdA",
    "PSA": "PSA",
    "POCH": "POCH",
    "GPS": "GP",
    "GRÜNE": "GP",
    "FGA": "FGA",
    "Sol.": "Sol",
    "SD": "SD",
    "Rep.": "Rep",
    "EDU": "EDU",
    "FPS": "FPS",
    "Lega": "Lega",
    "MCR": "MCR",
    "MCG (MCR)": "MCR",
    "LS": "LS",
    "JB": "JB",
    "Front": "Front",
    "Grüt": "Grüt",
    "Übrige": "others",
}
PARTIES = {
    "FDP": ("FDP.Die Liberalen", "FDP", "FDP.The Liberals"),
    "Mitte": ("Die Mitte", "Mitte", "The Centre"),
    "SP": ("Sozialdemokratische Partei der Schweiz", "SP", "Social Democratic Party of Switzerland"),
    "SVP": ("Schweizerische Volkspartei", "SVP", "Swiss People's Party"),
    "GP": ("GRÜNE Schweiz", "GRÜNE", "Green Party of Switzerland"),
    "GLP": ("Grünliberale Partei der Schweiz", "GLP", "Green Liberal Party of Switzerland"),
    "EVP": ("Evangelische Volkspartei der Schweiz", "EVP", "Evangelical People's Party of Switzerland"),
    "EDU": ("Eidgenössisch-Demokratische Union", "EDU", "Federal Democratic Union of Switzerland"),
    "SD": ("Schweizer Demokraten", "SD", "Swiss Democrats"),
    "CVP": (
        "Christlichdemokratische Volkspartei der Schweiz",
        "CVP",
        "Christian Democratic People's Party of Switzerland",
    ),
    "Dem": ("Demokraten", "Dem.", "Democrats"),
    "LPS": ("Liberale Partei der Schweiz", "LPS", "Liberal Party of Switzerland"),
    "LdU": ("Landesring der Unabhängigen", "LdU", "Alliance of Independents"),
    "CSP": ("Christlichsoziale Partei", "CSP", "Christian Social Party"),
    "BDP": ("Bürgerlich-Demokratische Partei", "BDP", "Conservative Democratic Party"),
    "PdA": ("Partei der Arbeit", "PdA", "Swiss Party of Labour"),
    "PSA": ("Parti socialiste autonome", "PSA", "Autonomous Socialist Party"),
    "POCH": ("Progressive Organisationen der Schweiz", "POCH", "Progressive Organizations of Switzerland"),
    "FGA": ("Feministische und grün-alternative Gruppierungen", "FGA", None),
    "Sol": ("Solidarität", "Sol.", None),
    "Rep": ("Schweizerische Republikanische Bewegung", "Rep.", "Swiss Republican Movement"),
    "FPS": ("Freiheits-Partei der Schweiz", "FPS", "Freedom Party of Switzerland"),
    "Lega": ("Lega dei Ticinesi", "Lega", "Ticino League"),
    "MCR": ("Mouvement Citoyens Genevois", "MCG", "Geneva Citizens' Movement"),
    "Sep": ("Sep.", "Sep.", None),
    "LS": ("Liberalsozialistische Partei (Freiwirtschafter)", "LS", None),
    "JB": ("Jungbauern", "JB", None),
    "Front": ("Nationale Front", "Front", None),
    "Grüt": ("Grütlianer", "Grüt", None),
}
OGD_PARTIES = PX_PARTIES | {"14": "SD"}
LIST_PARTIES = {("VD", "19"): "Sol", ("NE", "36"): "Sol", ("GE", "2"): "Sol"}
TURNOUT = {"1": 2, "2": 3, "4": 6, "5": 5, "6": 4, "7": 22, "8": 23}
UNALLOCATED = 32
SOURCE_NOTE = (
    "The Federal Statistical Office publishes party votes and ballots for every National Council election since "
    "1971, by canton and by commune, on today's communes and cantons: where communes merged after an election, "
    "their results are added together under the merged commune. Seats by party and canton go back to 1919. "
    "For 2023 it also publishes each candidate's votes by commune. Outside Zurich, candidates appear under their "
    "list's party, because a party often runs several lists in a canton (a youth or senior list, for example) and "
    "the archive does not yet record which list each candidate stood on. A cantonal source can add that later."
)
MAJORITY = (
    "A canton with a single seat elects its member by relative majority: the candidate with the most votes wins. "
    "Where only one candidate stands for a seat, they are declared elected without a vote (a tacit election)."
)
PARTY_LEVEL = (
    "The Federal Statistical Office reports these results by party, adding together a party's lists in the canton. "
    "Seats follow the list each member was elected on, not the parliamentary group they joined."
)
SEATS_ONLY = (
    "The Federal Statistical Office publishes seats by party for this election but not the votes, so only the "
    "declared seats are stored."
)
SHORT = (
    "The Federal Statistical Office gives no commune figures for Courrendlin before 1996: Vellerat, part of Bern "
    "until then, has since merged into it. The communes here add up to {votes} party votes and {ballots} ballots "
    "cast fewer than its total for {canton}. The seats are as declared."
)
COURRENDLIN = "6708"
JOINT = {
    (1999, "BE"): (
        "The Green Alliance (FGA) member was elected on a joint list with the Green Party. The Federal Statistical "
        "Office counts the joint list's votes under the Greens and gives the seat to the FGA, as here."
    )
}
TACIT = "The canton's seats were filled without a vote (a tacit election), because no more candidates stood than seats."


def query(table: str, year: int | None) -> dict[str, object]:
    geography = {"101": "Kanton (-) / Bezirk (>>) / Gemeinde (......)", "105": "Bezirk (>>) / Gemeinde (......)"}
    selections: list[dict[str, object]] = [
        {"code": geography.get(table, "Kanton"), "selection": {"filter": "all", "values": ["*"]}},
        {
            "code": "Jahr",
            "selection": {"filter": "item", "values": [str(year)] if year is not None else [str(y) for y in COUNTED]},
        },
    ]
    if table != "101":
        selections.append({"code": "Partei", "selection": {"filter": "all", "values": ["*"]}})
    selections.append(
        {"code": "Ergebnisse", "selection": {"filter": "item", "values": list(TURNOUT) if table == "101" else ["1"]}}
    )
    return {"query": selections, "response": {"format": "json-stat2"}}


REQUESTS = [("104", None)] + [(table, year) for table in ("101", "105") for year in COUNTED]


def fetch() -> None:
    for table, year in REQUESTS:
        raw.fetch(SOURCE, PX.format(table), suffix=".json", body=query(table, year))
    raw.fetch(SOURCE, SEATS, suffix=".xlsx")
    for name in ("listen", "kandidierende"):
        raw.fetch(SOURCE, OGD.format(name), suffix=".json")


def cells(table: str, year: int | None) -> list[tuple[list[str], int | None]]:
    data = json.loads(raw.cached(SOURCE, PX.format(table), query(table, year)).path.read_text(encoding="utf-8"))
    axes = [
        sorted(
            data["dimension"][dimension]["category"]["index"], key=data["dimension"][dimension]["category"]["index"].get
        )
        for dimension in data["id"]
    ]
    return [(list(codes), value) for codes, value in zip(itertools.product(*axes), data["value"], strict=True)]


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list[tuple[object, ...]]] = {
        "cantons": [],
        "communes": [],
        "turnout": [],
        "labels": [],
        "seats": [],
    }
    for (canton, year, party, _), value in cells("104", None):
        if canton != "CH" and value is not None:
            rows["cantons"].append((int(year), canton, PX_PARTIES[party], round(value)))
    owner: dict[str, str] = {}
    laufen: set[str] = set()
    for year in COUNTED:
        canton = district = ""
        data = json.loads(raw.cached(SOURCE, PX.format("101"), query("101", year)).path.read_text(encoding="utf-8"))
        geography = data["dimension"][data["id"][0]]["category"]
        for code in sorted(geography["index"], key=geography["index"].get):
            if len(code) == 2 and code != "CH":
                canton = code
            if len(code) == 6:
                district = geography["label"][code]
            if len(code) == 4:
                owner[code] = canton
                if canton == "BL" and district.endswith("Bezirk Laufen"):
                    laufen.add(code)
                rows["labels"].append((code, canton, geography["label"][code].lstrip(".- ")))

        def held(code: str, year: int = year) -> str:
            if owner[code] == "JU" and year < 1979 or code in laufen and year < 1994:
                return "BE"
            return owner[code]

        for (code, _, field), value in cells("101", year):
            if len(code) in (2, 4) and code != "CH" and value is not None:
                rows["turnout"].append(
                    (year, code if len(code) == 2 else held(code), code, TURNOUT[field], round(value))
                )
        for (code, _, party, _), value in cells("105", year):
            if len(code) == 4 and value is not None:
                rows["communes"].append((year, held(code), code, PX_PARTIES[party], round(value)))

    german = {label: code for code, (_, label) in CANTONS.items()}
    for sheet, frame in pl.read_excel(raw.cached(SOURCE, SEATS).path, sheet_id=0, has_header=False).items():
        if not sheet.isdigit():
            continue
        header = [re.sub(r"\s+\d+\)?$", "", str(cell)) if cell is not None else None for cell in frame.row(1)]
        footnotes = {
            match[1]
            for (cell,) in frame.select(frame.columns[0]).iter_rows()
            if cell is not None and (match := re.match(r"(\d+)\)?\s+Stille Wahl", str(cell)))
        }
        for row in frame.iter_rows():
            label = str(row[0] or "").strip()
            match = re.match(r"(.+?)(?:\s+(\d+)\)?)?$", label)
            if match is None or match[1] not in german:
                continue
            canton = german[match[1]]
            if row[header.index("Total")] is None:
                continue
            total = int(row[header.index("Total")])
            won: dict[str, int] = {}
            for column, cell in zip(header[1:], row[1:], strict=True):
                if column in (None, "Total") or cell is None or not str(cell).isdigit():
                    continue
                key = SHEET_PARTIES[column]
                won[key] = won.get(key, 0) + int(cell)
            if sum(won.values()) != total:
                raise ValueError(f"{sheet} {canton}: seats {won} do not add up to {total}")
            for key, count in won.items():
                rows["seats"].append((int(sheet), canton, key, count, total, match[2] in footnotes))

    cantons = (
        pl.DataFrame(rows["cantons"], schema=["year", "canton", "party", "votes"], orient="row")
        .group_by("year", "canton", "party")
        .agg(pl.col("votes").sum())
    )
    communes = pl.DataFrame(rows["communes"], schema=["year", "canton", "commune", "party", "votes"], orient="row")
    communes = communes.group_by("year", "canton", "commune", "party").agg(pl.col("votes").sum())
    turnout = pl.DataFrame(rows["turnout"], schema=["year", "canton", "unit", "measure", "value"], orient="row")
    labels = pl.DataFrame(rows["labels"], schema=["commune", "canton", "label"], orient="row").unique("commune")
    seats = pl.DataFrame(rows["seats"], schema=["year", "canton", "party", "seats", "magnitude", "tacit"], orient="row")

    party_gaps = (
        communes.group_by("year", "canton", "party")
        .agg(pl.col("votes").sum())
        .join(cantons, on=["year", "canton", "party"], how="full", suffix="_canton", coalesce=True)
        .select(
            "year",
            "canton",
            gap=pl.col("votes").fill_null(0) - pl.col("votes_canton").fill_null(0),
        )
        .filter(pl.col("gap") != 0)
    )
    unit_turnout = turnout.filter(
        pl.col("unit").str.len_chars() == 4, ~((pl.col("unit") == COURRENDLIN) & pl.col("year").is_between(1979, 1995))
    )
    ballot_gaps = (
        unit_turnout.group_by("year", "canton", "measure")
        .agg(pl.col("value").sum())
        .join(
            turnout.filter(pl.col("unit").str.len_chars() == 2).drop("unit"),
            on=["year", "canton", "measure"],
            how="full",
            suffix="_canton",
            coalesce=True,
        )
        .select(
            "year",
            "canton",
            "measure",
            gap=pl.col("value").fill_null(0) - pl.col("value_canton").fill_null(0),
        )
        .filter(pl.col("gap") != 0)
    )
    for gaps in (party_gaps, ballot_gaps):
        unexplained = gaps.filter(
            ~((pl.col("year") < 1996) & pl.col("canton").is_in(["BE", "JU"]) & (pl.col("gap") < 0))
        )
        if unexplained.height:
            raise ValueError(f"communes differ from their canton: {unexplained}")
    short = (
        party_gaps.group_by("year", "canton")
        .agg(pl.col("gap").sum())
        .join(
            ballot_gaps.filter(pl.col("measure") == 3).select("year", "canton", ballots="gap"),
            on=["year", "canton"],
            how="full",
            coalesce=True,
        )
    )
    unknown = seats.filter(pl.col("year") >= 1971, pl.col("seats") > 0).join(
        cantons, on=["year", "canton", "party"], how="anti"
    )
    unknown = unknown.filter(
        ~pl.col("tacit"), ~pl.any_horizontal([(pl.col("year") == y) & (pl.col("canton") == c) for y, c in JOINT])
    )
    if unknown.height:
        raise ValueError(f"seats for parties without votes: {unknown}")

    numbers = dict(enumerate(CANTONS, start=1))
    lists = pl.DataFrame(
        [
            (
                numbers[row["kanton_nummer"]],
                row["liste_nummer_kanton"],
                LIST_PARTIES.get(
                    (numbers[row["kanton_nummer"]], row["liste_nummer_kanton"]), OGD_PARTIES[str(row["partei_id"])]
                ),
                row["stimmen_liste"],
            )
            for row in json.loads(raw.cached(SOURCE, OGD.format("listen")).path.read_text(encoding="utf-8"))[
                "level_kantone"
            ]
            if row["kanton_nummer"] != 1
        ],
        schema=["canton", "list", "party", "votes"],
        orient="row",
    )
    mismatched = (
        lists.group_by("canton", "party")
        .agg(pl.col("votes").sum())
        .join(cantons.filter(pl.col("year") == 2023), on=["canton", "party"], how="full", coalesce=True)
        .filter(pl.col("votes").fill_null(0) != pl.col("votes_right").fill_null(0), pl.col("canton") != "ZH")
    )
    if mismatched.height:
        raise ValueError(f"2023 list votes differ from party votes: {mismatched}")
    data = json.loads(raw.cached(SOURCE, OGD.format("kandidierende")).path.read_text(encoding="utf-8"))
    parties = dict(zip(lists.select(pl.concat_str("canton", pl.lit(":"), "list")).to_series(), lists["party"]))
    candidates = pl.DataFrame(
        [
            (
                numbers[row["kanton_nummer"]],
                row["liste_nummer_kanton"],
                row["kandidat_nummer"],
                parties[f"{numbers[row['kanton_nummer']]}:{row['liste_nummer_kanton']}"],
                OGD_PARTIES.get(str(row["kandidat_partei_id"])),
                row["name"].strip(),
                row["vorname"].strip(),
                {"F": "female", "M": "male"}.get(row["geschlecht"]),
                None
                if row["geburtsdatum"] is None
                else date(*reversed([int(part) for part in row["geburtsdatum"].split(".")])),
                row["geburtsjahr"],
                1 if row["kandidat_listenplatz_2"] is None else 2,
                row["kandidat_status_id"] == 2,
                row["flag_gewaehlt"],
            )
            for row in data["level_kantone"]
            if row["kanton_nummer"] != 1
        ],
        schema=[
            "canton",
            "list",
            "number",
            "party",
            "own",
            "surname",
            "given",
            "gender",
            "born",
            "year",
            "lines",
            "incumbent",
            "elected",
        ],
        orient="row",
    )
    pl.DataFrame(
        [
            (
                numbers[row["kanton_nummer"]],
                row["liste_nummer_kanton"],
                row["kandidat_nummer"],
                f"{row['gemeinde_nummer'] % 10000:04d}",
                row["stimmen_kandidat"],
            )
            for row in data["level_gemeinden"]
            if row["kanton_nummer"] != 1
        ],
        schema=["canton", "list", "number", "commune", "votes"],
        orient="row",
    ).write_parquet(PARSED / "candidate_votes.parquet")
    del data
    candidates.write_parquet(PARSED / "candidates.parquet")
    communes.write_parquet(PARSED / "communes.parquet")
    unit_turnout.write_parquet(PARSED / "turnout.parquet")
    labels.write_parquet(PARSED / "labels.parquet")
    seats.write_parquet(PARSED / "seats.parquet")
    cantons.write_parquet(PARSED / "cantons.parquet")
    short.write_parquet(PARSED / "short.parquet")


def emit() -> Path:
    communes = pl.read_parquet(PARSED / "communes.parquet")
    turnout = pl.read_parquet(PARSED / "turnout.parquet")
    labels = pl.read_parquet(PARSED / "labels.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    cantons = pl.read_parquet(PARSED / "cantons.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    candidate_votes = pl.read_parquet(PARSED / "candidate_votes.parquet")
    shortfalls = {
        (year, canton): (gap, ballots)
        for year, canton, gap, ballots in pl.read_parquet(PARSED / "short.parquet").iter_rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Swiss National Council elections from 1919 to 2023 by canton: seats by party, and from 1971 party "
                "votes and ballots by commune"
            ),
            polity="CH",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, "Statistik der Nationalratswahlen")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title="Statistik der Nationalratswahlen",
            url="https://www.bfs.admin.ch/bfs/de/home/statistiken/politik/wahlen/eidgenoessische-wahlen/nationalrat.html",
            license="Open use; must provide the source",
            license_url="https://www.bfs.admin.ch/bfs/en/home/fso/swiss-federal-statistical-office/terms-of-use.html",
            notes=SOURCE_NOTE,
        )
        for table, year in REQUESTS:
            cs.artifact(raw.cached(SOURCE, PX.format(table), query(table, year)), source)
        cs.artifact(raw.cached(SOURCE, SEATS), source)
        for name_ in ("listen", "kandidierende"):
            cs.artifact(raw.cached(SOURCE, OGD.format(name_)), source)

        switzerland = cs.polity("CH")
        areas = {code: cs.area("ch.canton", code) for code in CANTONS}
        if cs.minted & {switzerland, *areas.values()}:
            raise ValueError("run ch.swisstopo first: Switzerland and its cantons must exist")

        units: dict[str, UUID] = {}
        channels: dict[str, int] = {}
        used = set(communes["commune"]) | set(turnout["unit"])
        for code, canton, label in labels.filter(pl.col("commune").is_in(list(used))).sort("commune").iter_rows():
            units[code] = cs.unit("ch.commune", code)
            channels[code] = 0
            if not code.startswith("9"):
                if units[code] in cs.minted:
                    area = cs.area("ch.commune", code)
                    cs.add(
                        "reporting_unit",
                        id=units[code],
                        polity_id=switzerland,
                        scheme_code="ch.commune",
                        code=code,
                        area_relation="within" if area in cs.minted else "equals",
                        area_id=areas[canton] if area in cs.minted else area,
                        kind="standard",
                        valid_from=None,
                    )
                    if area in cs.minted:
                        name(units[code], label, LANGUAGES.get(canton, "de"))
                continue
            postal = "Korrespondenz" in label or "corrispondenza" in label
            channels[code] = 3 if postal else 4
            if units[code] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=switzerland,
                    scheme_code="ch.commune",
                    code=code,
                    area_relation="within",
                    area_id=areas[canton],
                    kind="postal_district" if postal else "aggregate",
                    valid_from=None,
                )
                name(units[code], label, LANGUAGES.get(canton, "de"))

        body = cs.key("ch.body", "national-council")
        if body in cs.minted:
            raise ValueError("run ch.zh_statistik first: the National Council body must exist")
        sizes = seats.unique(["year", "canton"]).group_by("year").agg(pl.col("magnitude").sum()).sort("year")
        changes: list[tuple[date, int]] = []
        for year, size in sizes.iter_rows():
            if year < 1963 and (not changes or changes[-1][1] != size):
                changes.append((DAYS[year], size))
        for (start, size), end in zip(changes, [day for day, _ in changes[1:]] + [date(1963, 1, 1)], strict=True):
            cs.add(
                "body_size",
                body_id=body,
                seats=size,
                valid_from=start,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="year" if end == date(1963, 1, 1) else "day",
            )
        proportional = cs.key("ch.electoral_system", "national-council")
        if proportional in cs.minted:
            raise ValueError("run ch.zh_statistik first: the National Council electoral system must exist")
        majority = cs.key("ch.electoral_system", "national-council-single-seat")
        cs.add(
            "electoral_system",
            id=majority,
            polity_id=switzerland,
            rules={"formula": "fptp"},
            rules_version=1,
        )
        name(majority, "Swiss National Council single-seat majority vote")
        cs.add("note", entity_id=majority, kind="methodology", body_md=MAJORITY)

        parties: dict[str, UUID] = {}
        for key, (full, short, english) in PARTIES.items():
            parties[key] = cs.key("ch.party", key)
            if parties[key] in cs.minted:
                cs.add("party", id=parties[key], kind="party", polity_id=switzerland)
                name(parties[key], full, "de")
                name(parties[key], short, "de", "abbreviation")
                if english is not None:
                    name(parties[key], english, "en", "english")

        held = {
            str(day): election
            for day, election in conn.execute(
                "select e.date_from, e.id from election e join polity p on p.id = e.polity_id "
                "where p.iso_code = 'CH' and e.kind = 'referendum'"
            ).fetchall()
        }
        values: list[pl.DataFrame] = []
        for year, day in DAYS.items():
            election = cs.key("ch.election", f"{day}:federal" if str(day) in held else str(day))
            cs.add(
                "election",
                id=election,
                polity_id=switzerland,
                kind="general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{year} Swiss federal election")
            name(election, f"Eidgenössische Wahlen {year}", "de", "native")
            if election in cs.minted:
                cs.add("slug", path=f"ch/{day}-general", entity_id=election)
            for canton, (english, _) in CANTONS.items():
                frame = seats.filter(pl.col("year") == year, pl.col("canton") == canton)
                if frame.is_empty() or (year == 2023 and canton == "ZH"):
                    continue
                magnitude = frame["magnitude"][0]
                tacit = frame["tacit"][0]
                counted = year >= 1971 and not tacit
                contest_key = f"{day}:national-council:{canton}"
                contest = cs.key("ch.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=areas[canton],
                    magnitude=magnitude,
                    round_number=1,
                    electoral_system_id=majority if magnitude == 1 else proportional,
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{year} National Council election in {english}")
                cs.add(
                    "slug",
                    path=f"ch/{day}-general/national-council-{CANTONS[canton][0].lower().replace(' ', '-').replace('.', '')}",
                    entity_id=contest,
                )
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1 if counted else 0,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=PARTY_LEVEL)
                if tacit:
                    cs.add("note", entity_id=result_set, kind="context", body_md=TACIT)
                elif not counted:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=SEATS_ONLY)
                if (year, canton) in JOINT:
                    cs.add("note", entity_id=result_set, kind="context", body_md=JOINT[(year, canton)])
                if counted and (year, canton) in shortfalls:
                    gap, ballots = shortfalls[(year, canton)]
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=SHORT.format(
                            votes=f"{-(gap or 0):,}", ballots=f"{-(ballots or 0):,}", canton=CANTONS[canton][0]
                        ),
                    )
                standing = set(frame.filter(pl.col("seats") > 0)["party"])
                if counted:
                    standing |= set(cantons.filter(pl.col("year") == year, pl.col("canton") == canton)["party"])
                options: dict[str, str] = {}
                for key in sorted(standing):
                    option = cs.key("ch.option", f"{contest_key}:{key}")
                    options[key] = str(option)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if key == "others" else "list",
                        party_id=None if key == "others" else parties[key],
                        ballot_label="Übrige" if key == "others" else PARTIES[key][1],
                    )
                for key, count in frame.filter(pl.col("seats") > 0).select("party", "seats").iter_rows():
                    cs.add("outcome", result_set_id=result_set, option_id=options[key], kind="won", seats=count)
                standing_by: dict[str, str] = {}
                for row in candidates.filter(pl.col("canton") == canton).iter_rows(named=True) if year == 2023 else ():
                    key = f"{contest_key}:{row['list']}.{row['number']}"
                    option = cs.key("ch.option", key)
                    standing_by[f"{row['list']}.{row['number']}"] = str(option)
                    display = f"{row['given']} {row['surname']}"
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=UUID(options[row["party"]]),
                        ballot_label=display,
                    )
                    person = cs.key(f"{SOURCE}.person", key)
                    cs.add(
                        "person",
                        id=person,
                        birth_date=row["born"] or (None if row["year"] is None else date(row["year"], 1, 1)),
                        birth_precision="day" if row["born"] is not None else "year" if row["year"] else None,
                        gender=row["gender"],
                    )
                    name(person, display, LANGUAGES.get(canton, "de"))
                    candidacy = cs.key("ch.candidacy", key)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        list_position=row["number"],
                        ballot_name=f"{row['surname']} {row['given']}",
                        member_of_party_id=parties.get(row["own"]) if row["own"] != "others" else None,
                        is_independent=False,
                        is_incumbent=row["incumbent"],
                        ballot_lines=row["lines"],
                    )
                    if row["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if counted:
                    located = {
                        "result_set_id": pl.lit(str(result_set)),
                        "channel": pl.col("unit").replace_strict(channels, return_dtype=pl.Int16),
                        "count_round": pl.lit(0, pl.Int16),
                        "flags": pl.when(pl.col("unit").str.starts_with("9"))
                        .then(UNALLOCATED)
                        .otherwise(0)
                        .cast(pl.Int16),
                    }
                    party_votes = communes.filter(pl.col("year") == year, pl.col("canton") == canton).rename(
                        {"commune": "unit"}
                    )
                    ballots = turnout.filter(pl.col("year") == year, pl.col("canton") == canton)
                    values.append(
                        party_votes.select(
                            **located,
                            unit_id=pl.col("unit").replace_strict({c: str(u) for c, u in units.items()}),
                            option_id=pl.col("party").replace_strict(options),
                            measure=pl.lit(8, pl.Int16),
                            value="votes",
                        )
                    )
                    if standing_by:
                        values.append(
                            candidate_votes.filter(pl.col("canton") == canton)
                            .rename({"commune": "unit"})
                            .select(
                                **located,
                                unit_id=pl.col("unit").replace_strict({c: str(u) for c, u in units.items()}),
                                option_id=pl.concat_str(pl.col("list"), pl.lit("."), pl.col("number")).replace_strict(
                                    standing_by
                                ),
                                measure=pl.lit(10, pl.Int16),
                                value="votes",
                            )
                        )
                    values.append(
                        ballots.select(
                            **located,
                            unit_id=pl.col("unit").replace_strict({c: str(u) for c, u in units.items()}),
                            option_id=pl.lit(None, pl.String),
                            measure=pl.col("measure").cast(pl.Int16),
                            value="value",
                        )
                    )
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
