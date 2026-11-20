"""European Parliament elections in Romania of 2009 and 2014, by polling station, from the Permanent Electoral
Authority's older open data files (Excel workbooks on data.gov.ro).

The seats won by each list in 2014 come from the Central Electoral Bureau's record of the count and the allocation of
seats, published in Monitorul Oficial nr. 399 of 29 May 2014 and read here from the text of that issue. Its national
totals are compared with the polling stations, and the build fails on any difference."""

import html
import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
import xlrd

from ea import package, raw
from ea.package import Package
from ea.sources.ro.aep import ALL, CAST, INVALID, REGISTERED, VALID, VOTES, canon

SOURCE = "ro.ep"
ROMANIA = str(uuid5(package.NAMESPACE, "polity\x1fRO"))
PORTAL = "https://data.gov.ro/organization/autoritatea-electorala-permanenta"
HOST = "http://data.gov.ro/storage/f"

URLS = {
    "2009-stations": f"{HOST}/2014-06-12T07:32:03.787Z/xsv-date.xls",
    "2009-legend": f"{HOST}/2014-06-12T07:34:47.895Z/structura-baza-date.xls",
    "2014-stations": f"{HOST}/2014-06-11T07:48:12.888Z/siaep2014-stat-statistica-la-nivel-de-sectie-de-votare1.xls",
    "2014-counties": f"{HOST}/2014-06-11T07:50:35.016Z/siaep2014-stat-statistica-la-nivel-de-tara-pe-birou-electoral.xls",
}
MINUTES = {"2014-05-25": "https://legeaz.net/monitorul-oficial-399-2014/proces-verbal-2014"}
DAYS = {"2009-06-07": date(2009, 6, 7), "2014-05-25": date(2014, 5, 25)}
SEATS = {"2009-06-07": 33, "2014-05-25": 32}
FIRST_INDEPENDENT_2014 = 31
FIX = str.maketrans({"Ş": "Ș", "ş": "ș", "Ţ": "Ț", "ţ": "ț", "Ã": "Ă", "ã": "ă"})
METHOD = (
    "Counted per polling station and published by the Permanent Electoral Authority as open data. Registered voters "
    "are the voters on the permanent lists and the copies of the special lists. Ballots cast are the voters who took "
    "part, valid votes are the votes for lists and candidates, and invalid ballots are the null votes. Stations "
    "abroad print no list of registered voters, so their registered voters are left empty."
)

SEATS_METHOD = (
    " The seats are those the Central Electoral Bureau declared in its record of the count and the allocation of "
    "seats, published in Monitorul Oficial nr. 399 of 29 May 2014. That record's registered voters, ballots cast, "
    "valid and null votes, and the votes of every list and independent candidate equal the sums of the polling "
    "stations."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


EP_SOURCE = key("source", "aep")
BEC_SOURCE = key("source", "bec")
SYSTEM = key("electoral_system", "ep-list")


def fix(text: object) -> str:
    return " ".join(str(text or "").translate(FIX).split())


def party_id(label: str) -> str:
    return key("party", canon(label))


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)
    for url in MINUTES.values():
        raw.fetch(SOURCE, url, suffix=".html")


def packages() -> list[str]:
    return ["ro.ep/registry", *(f"ro.ep/{day}" for day in DAYS)]


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def workbook(name: str) -> tuple[list[str], list[list[object]]]:
    sheet = xlrd.open_workbook(str(raw.cached(SOURCE, URLS[name]).path)).sheet_by_index(0)
    header = [str(cell).strip() for cell in sheet.row_values(0)]
    return header, [sheet.row_values(row) for row in range(1, sheet.nrows)]


def whole(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value)))


def legend_2009() -> list[tuple[str, str]]:
    """The 2009 ballot lines in printed order, from the layout of the data file: (column, label, independent?)."""
    sheet = xlrd.open_workbook(str(raw.cached(SOURCE, URLS["2009-legend"]).path)).sheet_by_index(0)
    lines: dict[str, list[str]] = {}
    column = ""
    for row in range(sheet.nrows):
        cells = sheet.row_values(row)
        if re.fullmatch(r"P\d+", str(cells[1]).strip()):
            column = str(cells[1]).strip()
            lines[column] = []
        elif column and not str(cells[0]).strip() and str(cells[3]).strip():
            lines[column].append(str(cells[3]).strip())
        elif str(cells[0]).strip() and not re.fullmatch(r"P\d+", str(cells[1]).strip()):
            column = ""
    found = []
    for column, text in lines.items():
        first = fix(text[0])
        if first.startswith("CI ") and len(text) > 1:
            found.append((column, fix(text[-1])))
        else:
            found.append((column, re.sub(r"^.*?\((.*)\)$", r"\1", first)))
    return found


def lines_2009() -> list[tuple[str, str, bool]]:
    return [(column, label, label.upper().startswith(("BĂSESCU", "ABRAHAM"))) for column, label in legend_2009()]


def lines_2014() -> list[tuple[str, str, bool]]:
    header, _ = workbook("2014-stations")
    return [
        (column, fix(column), position >= FIRST_INDEPENDENT_2014) for position, column in enumerate(header[16:], 16)
    ]


def registry() -> Package:
    p = Package(
        "ro.ep/registry",
        SOURCE,
        "Romanian parties and alliances that ran in the European Parliament elections of 2009 and 2014, and their "
        "electoral system, from the Permanent Electoral Authority's open data",
    )
    p.external.add(ROMANIA)
    p.add(
        "source",
        id=EP_SOURCE,
        kind="official_body",
        publisher="Autoritatea Electorală Permanentă",
        title="European Parliament election results published as open data by the Permanent Electoral Authority",
        url=PORTAL,
        license="Open Government Licence 1.0 (data.gov.ro)",
        attribution="Autoritatea Electorală Permanentă",
    )
    for url in URLS.values():
        p.artifact(raw.cached(SOURCE, url), EP_SOURCE)
    p.add(
        "source",
        id=BEC_SOURCE,
        kind="official_body",
        publisher="Biroul Electoral Central",
        title="Record of the count and the allocation of seats in the European Parliament election of 25 May 2014, "
        "Monitorul Oficial al României, Partea I, nr. 399 of 29 May 2014",
        url=MINUTES["2014-05-25"],
        license="Official text, not subject to copyright (Law 8/1996, art. 9)",
        attribution="Biroul Electoral Central",
    )
    for url in MINUTES.values():
        p.artifact(raw.cached(SOURCE, url), BEC_SOURCE)
    p.add("scheme", code="ro.ep_sv", polity_id=ROMANIA, kind="precinct", rank=2, noun="polling station")
    p.add("electoral_system", id=SYSTEM, polity_id=ROMANIA, rules={"formula": "dhondt"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Romania elects its members of the European Parliament from national party lists in one "
        "constituency. Thresholds and the allocation of seats are not stored here.",
        lang="en",
    )
    labels: dict[str, str] = {}
    for _, label, independent in [*lines_2009(), *lines_2014()]:
        if not independent:
            labels.setdefault(canon(label), label)
    for identifier, label in sorted(((party_id(canon(c)), c) for c in labels.values()), key=lambda item: item[1]):
        p.add("party", id=identifier, kind="bloc" if canon(label).startswith("ALIAN") else "party", polity_id=ROMANIA)
        p.name(identifier, label, "ro", "official")
    return p


def build(day: str) -> Package:
    first = DAYS[day]
    p = Package(
        f"ro.ep/{day}",
        SOURCE,
        f"The Romanian European Parliament election of {first} by polling station, from the Permanent Electoral "
        "Authority's open data",
        depends=["ro.ep/registry"],
    )
    p.external.add(ROMANIA)
    if day == "2009-06-07":
        frame, lines = stations_2009(p)
    else:
        frame, lines = stations_2014(p)
    frame = unit_rows(p, frame, day)
    election = key("election", day)
    p.add("election", id=election, polity_id=ROMANIA, kind="general", date_from=first, date_to=first, status="held")
    p.add("slug", path=f"ro/{day}-european", entity_id=election, is_current=True)
    p.name(election, f"{first.year} European Parliament election in Romania", "en", "official")
    p.name(election, f"Alegeri pentru Parlamentul European din {first:%d.%m.%Y}", "ro", "native", False)
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        round_number=1,
        default_result_kind="counted",
        magnitude=SEATS[day],
    )
    p.name(contest, f"{first.year} European Parliament election in Romania", "en", "official")
    p.name(contest, "Alegeri pentru Parlamentul European, România", "ro", "native", False)
    p.add("slug", path=f"ro/{day}-european/romania", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=EP_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = METHOD + (SEATS_METHOD if day in MINUTES else "")
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    options: dict[str, str] = {}
    for position, (column, label, independent) in enumerate(lines, 1):
        option = key("option", day, position)
        options[column] = option
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate" if independent else "list",
            party_id=None if independent else party_id(label),
            ballot_position=position,
            ballot_label=label.title() if independent else label,
        )
        if independent:
            who = key("person", day, label)
            p.add("person", id=who)
            p.name(who, label.title(), "ro", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, label),
                person_id=who,
                option_id=option,
                role="head",
                ballot_name=label.title(),
                is_independent=True,
            )
    stage(p, result_set, frame, options)
    if day in MINUTES:
        declare(p, day, result_set, frame, lines, options)
    p.accept(
        "accounting.ballots_sum",
        "some polling stations print valid and null votes that do not add up to the voters who took part; stored as "
        "printed",
    )
    p.accept(
        "accounting.turnout",
        "some polling stations print more voters than registered voters on the permanent and special lists, mostly "
        "on the supplementary lists and abroad; stored as printed",
    )
    return p


def unit_rows(p: Package, frame: pl.DataFrame, day: str) -> pl.DataFrame:
    """Add each polling station once, and return the frame with its unit id."""
    frame = frame.with_columns(pl.format("{}:{}/{}", pl.lit(day), pl.col("circ"), pl.col("nr")).alias("code"))
    units = frame.select("code", "circ", "cname", "nr", "place")
    if units["code"].n_unique() != frame.height:
        raise ValueError(f"{day}: duplicate station codes")
    ids = [key("unit", code) for code in units["code"]]
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": ids,
                "polity_id": ROMANIA,
                "scheme_code": "ro.ep_sv",
                "code": units["code"],
                "area_relation": "unlocated",
                "kind": "standard",
            }
        ),
    )
    names = []
    for circ, cname, nr, place in units.select("circ", "cname", "nr", "place").iter_rows():
        where = " ".join(str(place or "").split())
        county = cname.title() if cname else str(circ)
        names.append(f"{where} ({county}, station {nr})" if where else f"{county}, station {nr}")
    p.frame(
        "name",
        pl.DataFrame(
            {
                "id": [p.id("name", unit, "ro", "official", text) for unit, text in zip(ids, names, strict=True)],
                "entity_id": ids,
                "text": names,
                "lang": "ro",
                "kind": "official",
                "is_preferred": True,
            }
        ),
    )
    return frame.with_columns(pl.Series("unit", ids))


def stage(p: Package, result_set: str, frame: pl.DataFrame, options: dict[str, str]) -> None:
    parts = []
    for column, measure in (("registered", REGISTERED), ("cast", CAST), ("valid", VALID), ("invalid", INVALID)):
        parts.append(
            frame.filter(pl.col(column).is_not_null()).select(
                "unit",
                pl.col(column).alias("value"),
                pl.lit(measure, pl.Int16).alias("measure"),
                pl.lit(None, pl.String).alias("option_id"),
            )
        )
    for column, option in options.items():
        parts.append(
            frame.filter(pl.col(column).is_not_null()).select(
                "unit",
                pl.col(column).alias("value"),
                pl.lit(VOTES, pl.Int16).alias("measure"),
                pl.lit(option).alias("option_id"),
            )
        )
    stacked = pl.concat(parts)
    p.frame(
        "result_value",
        stacked.select(
            pl.lit(result_set).alias("result_set_id"),
            pl.col("unit").alias("unit_id"),
            "option_id",
            pl.lit(ALL, pl.Int16).alias("channel"),
            "measure",
            pl.lit(0, pl.Int16).alias("count_round"),
            pl.lit(0, pl.Int16).alias("flags"),
            "value",
        ),
    )


def frame_of(rows: list[dict[str, object]], columns: list[str]) -> pl.DataFrame:
    schema: dict[str, type[pl.Int64]] = {
        "circ": pl.Int64,
        "registered": pl.Int64,
        "cast": pl.Int64,
        "valid": pl.Int64,
        "invalid": pl.Int64,
        "blank": pl.Int64,
        **{column: pl.Int64 for column in columns},
    }
    return pl.DataFrame(rows, schema_overrides=schema).with_columns(
        pl.lit(False).alias("postal"),
        pl.when(pl.col("registered") > 0).then(pl.col("registered")).otherwise(None).alias("registered"),
        pl.lit(None, pl.String).alias("siruta"),
    )


def stations_2009(p: Package) -> tuple[pl.DataFrame, list[tuple[str, str, bool]]]:
    for name in ("2009-stations", "2009-legend"):
        p.uses(raw.cached(SOURCE, URLS[name]))
    lines = lines_2009()
    header, rows = workbook("2009-stations")
    at = {name: header.index(name) for name in header}
    built: list[dict[str, object]] = []
    for row in rows:
        if row[at["NSV"]] in ("", None):
            continue
        place = fix(f"{row[at['DENLOC']]} — {row[at['ADRESA']]}") if row[at["ADRESA"]] else fix(row[at["DENLOC"]])
        built.append(
            {
                "circ": whole(row[at["NCE"]]),
                "cname": fix(row[at["JUDET"]]),
                "nr": str(whole(row[at["NSV"]])),
                "place": place,
                "registered": whole(row[at["A"]]),
                "cast": whole(row[at["B"]]),
                "valid": whole(row[at["E"]]),
                "invalid": whole(row[at["F"]]),
                "blank": None,
                **{column: whole(row[at[column]]) for column, _, _ in lines},
            }
        )
    return frame_of(built, [column for column, _, _ in lines]), lines


def stations_2014(p: Package) -> tuple[pl.DataFrame, list[tuple[str, str, bool]]]:
    for name in ("2014-stations", "2014-counties"):
        p.uses(raw.cached(SOURCE, URLS[name]))
    header, rows = workbook("2014-stations")
    lines = lines_2014()
    at = {name: header.index(name) for name in header}
    built: list[dict[str, object]] = []
    totals: list[object] = []
    for row in rows:
        if row[at["Cod Birou Electoral"]] in ("", None):
            totals = row
            continue
        built.append(
            {
                "circ": whole(row[at["Cod Birou Electoral"]]),
                "cname": fix(row[at["Nume Judet"]]),
                "nr": str(whole(row[at["Nr Sectie Votare"]])),
                "place": fix(row[at["Localitate"]]),
                "registered": whole(row[at["Nr Alegatori Liste"]]),
                "cast": whole(row[at["Nr Alegatori Prezentati La Urne"]]),
                "valid": whole(row[at["Total Voturi Valabil Exprimate La Nivel Sectie"]]),
                "invalid": whole(row[at["Nr Voturi Nule"]]),
                "blank": None,
                **{column: whole(row[at[column]]) for column, _, _ in lines},
            }
        )
    frame = frame_of(built, [column for column, _, _ in lines])
    check_2014(frame, header, totals)
    return frame, lines


def check_2014(frame: pl.DataFrame, header: list[str], totals: list[object]) -> None:
    """Compare the stations with the Authority's printed national and county totals; fail on any difference."""
    names = {
        "registered": "Nr Alegatori Liste",
        "cast": "Nr Alegatori Prezentati La Urne",
        "valid": "Total Voturi Valabil Exprimate La Nivel Sectie",
        "invalid": "Nr Voturi Nule",
    }
    summed = frame.select(*(pl.col(c).sum() for c in ("cast", "valid", "invalid")))
    for column, name in names.items():
        if column == "registered":
            continue
        printed = whole(totals[header.index(name)])
        if summed[column][0] != printed:
            raise ValueError(f"2014 {column}: stations sum to {summed[column][0]}, the Authority prints {printed}")
    for column, _, _ in lines_2014():
        printed = whole(totals[header.index(column)])
        if frame[column].sum() != printed:
            raise ValueError(f"2014 {column}: stations sum to {frame[column].sum()}, the Authority prints {printed}")
    head, counties = workbook("2014-counties")
    for row in counties:
        if row[0] in ("", None):
            continue
        part = frame.filter(pl.col("circ") == whole(row[0]))
        for column, name in names.items():
            name = "Total Voturi Valabil Exprimate" if column == "valid" else name
            if column != "registered" and part[column].sum() != whole(row[head.index(name)]):
                raise ValueError(f"2014 county {row[0]} {column} differs from the county file")
        for column, _, _ in lines_2014():
            if part[column].sum() != whole(row[head.index(column)]):
                raise ValueError(f"2014 county {row[0]} {column} differs from the county file")


def plain(text: str) -> str:
    """A label without diacritics or spacing differences, for matching the record's names with the data files'."""
    folded = unicodedata.normalize("NFKD", canon(text))
    return "".join(char for char in folded if not unicodedata.combining(char))


def cells(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def minutes(day: str) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """The record's national totals (points a, b, e, f), the valid votes of each list (point g), and the seats of each
    list (point k), keyed by plain label. The record prints each number one digit to a box."""
    page = raw.cached(SOURCE, MINUTES[day]).path.read_text(encoding="utf-8")
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.DOTALL)
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())
    start = text.index("PROCES - VERBAL privind centralizarea voturilor")
    text = text[start:]

    def between(first: str, last: str) -> str:
        at = text.index(first)
        return text[at : text.index(last, at)]

    totals = {}
    for point, label, end in (
        ("registered", " a Numărul total al alegătorilor înscriși", " a1 Numărul"),
        ("cast", " b Numărul total al alegătorilor înscriși în listele electorale, care", " b1 Numărul"),
        ("valid", " e Numărul voturilor valabil exprimate", " f Numărul"),
        ("invalid", " f Numărul voturilor nule", " g. Numărul"),
    ):
        part = between(label, end)
        totals[point] = cells(part[part.index("|") :])

    def rows(part: str) -> dict[str, int]:
        found = {
            plain(name): cells(boxes)
            for _, name, boxes in re.findall(r"(?:^| )(\d+) ([^|\d]+?) ((?:[|_\d ])*\|)", part)
        }
        if not found:
            raise ValueError(f"{day}: no rows in the record")
        return found

    votes = rows(between(" g. ", " h. ").split("obținute", 1)[1])
    seats = rows(between(" k. ", " Total ").split("Număr mandate atribuite", 1)[1])
    printed = cells(between(" k. ", " 1. ").split(" Total ", 1)[1])
    if sum(seats.values()) != printed or printed != SEATS[day]:
        raise ValueError(f"{day}: the record's seats sum to {sum(seats.values())}, it prints {printed}")
    return totals, votes, seats


def declare(
    p: Package,
    day: str,
    result_set: str,
    frame: pl.DataFrame,
    lines: list[tuple[str, str, bool]],
    options: dict[str, str],
) -> None:
    """Store the seats the Central Electoral Bureau declared, after checking its totals against the stations."""
    p.uses(raw.cached(SOURCE, MINUTES[day]))
    totals, votes, seats = minutes(day)
    for column, printed in totals.items():
        summed = frame[column].sum()
        if summed != printed:
            raise ValueError(f"{day} {column}: stations sum to {summed}, the record prints {printed}")
    columns = {plain(label): column for column, label, _ in lines}
    if set(votes) != set(columns):
        raise ValueError(f"{day}: the record's lists differ from the data file's: {set(votes) ^ set(columns)}")
    for label, printed in votes.items():
        if frame[columns[label]].sum() != printed:
            raise ValueError(
                f"{day} {label}: stations sum to {frame[columns[label]].sum()}, the record prints {printed}"
            )
    for label, won in seats.items():
        p.add("outcome", result_set_id=result_set, option_id=options[columns[label]], kind="won", seats=won)
