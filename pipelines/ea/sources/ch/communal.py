"""Municipal parliament elections in Switzerland.

The Statistical Office of the canton of Zürich publishes every municipal parliament election the cantonal count
software handled (2018, 2022, and 2026): turnout and ballots by counting district, votes by list and by candidate,
and the seats each list won.
"""

import functools
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

import polars as pl
import shapely
import shapely.geometry
from shapely.geometry import shape as geometry_shape

from ea import changeset, raw
from ea.package import Package

SOURCE = "ch.communal"
ZURICH_AREA = "01a13b73-f784-72a8-85f0-2a23e50a492d"
UNITS_CITY = {
    "1+2": (1, 2),
    "3": (3,),
    "4+5": (4, 5),
    "6": (6,),
    "7+8": (7, 8),
    "9": (9,),
    "10": (10,),
    "11": (11,),
    "12": (12,),
}
COUNT_SCHEME = "ch-zh.count_district"
# Production holds Switzerland under the id its first pipeline gave it, not the derived one.
CH = "01a13b73-f75e-7177-8281-2867f60d0ee8"
OGD = "https://daten.statistik.zh.ch/ogd/daten/ressourcen/KTZH_00001282_{}"
DOCUMENTATION = OGD.format("00002422.xlsx")
DATASET = "https://www.web.statistik.zh.ch/ogd/daten/"
STADTKREISE = (
    "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/Stadtkreise?service=WFS&version=1.1.0&request=GetFeature"
    "&typename=adm_stadtkreise_v&outputFormat=GeoJSON&srsName=EPSG:4326"
)
STADTKREISE_SOURCE = "ch.communal.stadtkreise"
CITY = "https://data.stadt-zuerich.ch/dataset/politik_gemeinderatswahlen_{y}_resultate/download/GRW_{y}_resultate_kandidierende_und_herkunft_der_stimmen.csv"
CITY_DAYS = {"2010-03-07": 2010, "2014-02-09": 2014}
YEARS = {
    2018: ("00006363", "00006364", "00006365"),
    2022: ("00002438", "00002418", "00002420"),
    2026: ("00006815", "00006816", "00006817"),
}
# The communes of the canton of Zürich with a parliament, by BFS number: name and the area of the commune.
COMMUNES = {
    "53": ("Bülach", "01a13b73-f77c-7380-903b-8918e7845e3b"),
    "62": ("Kloten", "01a13b73-f77c-7380-903b-8877bf238b28"),
    "66": ("Opfikon", "01a13b73-f77e-77c1-b3ac-825628f19d12"),
    "121": ("Wetzikon", "01a13b73-f780-7312-a520-24de6410e422"),
    "131": ("Adliswil", "01a13b73-f77c-7380-903b-8851026a9de0"),
    "191": ("Dübendorf", "01a13b73-f784-72a8-85f0-2aa9b202646c"),
    "198": ("Uster", "01a13b73-f784-72a8-85f0-2a0de0966c32"),
    "230": ("Winterthur", "01a13b73-f77c-7380-903b-888ea1b053fa"),
    "243": ("Dietikon", "01a13b73-f783-7692-9b18-f31c113919ca"),
    "247": ("Schlieren", "01a13b73-f784-72a8-85f0-2a419ac1ee36"),
    "261": ("Zürich", ZURICH_AREA),
    "293": ("Wädenswil", "01a13b73-f77d-73e5-ae35-ba22d0fea2bc"),
    "296": ("Illnau-Effretikon", "01a13b73-f780-7312-a520-24d9c33b6aa6"),
}
ZURICH = "261"
MONTHS = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]
UNIT_MEASURES = {
    "Stimmberechtigte": 2,
    "StmAbgegeben": 3,
    "StmGueltig": 4,
    "StmUngueltig": 5,
    "StmLeer": 6,
    "AnzWZUnveraendert": 22,
    "AnzWZVeraendert": 23,
}
LIST_BALLOTS = {"AnzWZUnveraendertListe": 22, "AnzWZVeraendertListe": 23}
VOTES_VALID = (
    "Every voter has as many votes as the parliament has seats, so the votes of the lists add up to the valid "
    "ballots times the seats in every counting district, exactly, and not to the number of valid ballots."
)
VOTES_VALID_2026 = (
    "Every voter has as many votes as the parliament has seats, so the votes of the lists add up to the valid "
    "ballots times the seats, and not to the number of valid ballots. In every counting district they fall short "
    "of that product, by no more than the seats times the ballots that carry no list name, whose blank lines count "
    "for no list."
)
SOURCE_CITY = SOURCE + ".zurich"
CITY_METHOD = (
    "Counted by the city of Zürich and published by Statistik Stadt Zürich with the votes each candidate received in "
    "the district and whether the candidate was elected. The file gives no votes by list, no ballots, and no seats by "
    "list, so each list's seats are the candidates it elected. Five members of 2010 who moved up after the election "
    "are not counted as elected."
)
SYSTEM_RULES = {"formula": "hagenbach_bischoff", "lists": {"order": "open", "vacancy": "next_by_votes"}}
SYSTEM_NOTE = (
    "Municipal parliaments of the canton of Zürich are elected by proportional representation with open lists: "
    "voters write candidates on a list, cumulate a candidate up to two times, and add candidates of other lists "
    "(panachage). A list's votes are its candidates' votes plus the additional votes from the blank lines of ballots "
    "that carry its name. Lists first take seats as list groups where they are linked, and each list's seats go to "
    "its candidates with the most votes."
)
METHOD = (
    "Counted by each municipality and compiled by the Statistical Office of the canton of Zürich. Registered voters "
    "are the electoral roll in the counting district, ballots cast are the ballots put in, and valid ballots are the "
    "ballots that counted. A list's votes are its candidates' votes plus its additional votes. Candidates "
    "cumulated twice appear once with two ballot lines. The office states that the municipalities entered the data "
    "and it did not check it, and that it shows the results on election day, not the composition of the parliament "
    "afterward."
)
ZURICH_NOTE = (
    "The city of Zürich elects its 125 council members in nine districts that join the twelve city districts "
    "(Stadtkreise), and it divides the seats among lists and districts with the double-proportional method. The "
    "seats each list won in each district are stored as declared; the electoral rules of this contest are not "
    "stored, because the engine cannot express the method."
)


def url(number: str) -> str:
    return OGD.format(f"{number}.csv")


def fetch() -> None:
    raw.fetch(SOURCE, DOCUMENTATION, suffix=".xlsx")
    for numbers in YEARS.values():
        for number in numbers:
            raw.fetch(SOURCE, url(number), suffix=".csv")
    for year in CITY_DAYS.values():
        raw.fetch(SOURCE, CITY.format(y=year), suffix=".csv")
    raw.fetch(STADTKREISE_SOURCE, STADTKREISE, suffix=".json")


# The 2018 files number the municipality of Wädenswil before its 2019 merger with Hütten and Schönenberg as 142.
RENUMBERED = {"142": "293"}


def table(number: str) -> pl.DataFrame:
    frame = pl.read_csv(raw.cached(SOURCE, url(number)).path, infer_schema_length=0)
    return frame.with_columns(pl.col("BfsNrGemeinde").replace(RENUMBERED))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in election_days())]


@functools.cache
def election_days() -> list[str]:
    days: set[str] = set()
    for _, candidates, _ in YEARS.values():
        days |= set(table(candidates)["Sonntag"].to_list())
    return sorted(days | set(CITY_DAYS))


def parse() -> None:
    geo().write()
    registry().write()
    for day in election_days():
        election(day).write()


def emit() -> None:
    from ea import package

    package.run("load", packages())


# The districts of the city of Zürich: each joins one or two city districts.
def wahlkreis(text: str) -> str:
    numbers = re.findall(r"\d+", text)
    if not numbers:
        raise ValueError(f"no district number in {text!r}")
    return "+".join(numbers)


def district_area(code: str) -> str:
    return str(Package("x", SOURCE, "").id("area", "zurich_district", code))


def _id(kind: str, *parts: object) -> str:
    return Package("x", SOURCE, "").id(kind, *parts)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The nine council districts of the city of Zürich, joined from its twelve city districts",
    )
    p.external |= {CH, ZURICH_AREA}
    source = _id("source", STADTKREISE_SOURCE)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Stadt Zürich, Geomatik + Vermessung",
        title="Stadtkreise (Open Government Data)",
        url="https://data.stadt-zuerich.ch/dataset/geo_stadtkreise",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Stadt Zürich, Geomatik + Vermessung",
    )
    artifact = raw.cached(STADTKREISE_SOURCE, STADTKREISE)
    p.artifact(artifact, source)
    scheme = "ch-zh.zurich_district"
    p.add(
        "scheme",
        code=scheme,
        polity_id=CH,
        kind="electoral_district",
        rank=4,
        noun="council district",
        code_system=None,
    )
    p.add(
        "scheme",
        code=COUNT_SCHEME,
        polity_id=CH,
        kind="precinct",
        rank=5,
        noun="counting district",
        code_system=None,
    )
    since = date(2006, 1, 1)
    p.add(
        "boundary_set",
        id=_id("boundary_set", scheme, 2006),
        scheme_code=scheme,
        vintage="2006",
        valid_from=since,
        as_of=since,
    )
    features = json.loads(artifact.path.read_text())["features"]
    kreise = {int(f["properties"]["knr"]): geometry_shape(f["geometry"]) for f in features}
    for code, members in sorted(UNITS_CITY.items()):
        id = district_area(code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code, valid_from=since)
        p.add("area_parent", area_id=id, parent_id=ZURICH_AREA, valid_from=since)
        text = "Kreis " + " + ".join(str(n) for n in members)
        p.name(id, f"Zürich, Wahlkreis {code.replace('+', ' + ')}", "de", "official")
        p.name(id, text, "de", "short", False)
        union = shapely.union_all([kreise[n] for n in members])
        parts = [x for x in shapely.get_parts(shapely.make_valid(union)) if shapely.get_type_id(x) in (3, 6)]
        outline = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=since,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=source,
        )
    return p


def _seats() -> dict[tuple[str, str], int]:
    """Seats of each parliament on each election day, summed over the contests of the commune."""
    seats: dict[tuple[str, str], int] = {}
    for _, candidates, _ in YEARS.values():
        frame = table(candidates).select("BfsNrGemeinde", "GeLfNr", "Sonntag", "Mandate").unique()
        for (bfs, day), group in frame.group_by("BfsNrGemeinde", "Sonntag"):
            seats[(str(bfs), str(day))] = sum(int(n) for n in group["Mandate"])
    for day in CITY_DAYS:
        seats[(ZURICH, day)] = sum(1 for row in city_rows(day) if row["elected"])
    return seats


def body_id(bfs: str) -> str:
    return _id("body", bfs)


def party_id(bfs: str, code: str) -> str:
    return _id("party", bfs, code)


def _lists() -> pl.DataFrame:
    parts = []
    for year, (_, candidates, lists) in YEARS.items():
        frame = table(lists)
        days = table(candidates).select("GeLfNr", "BfsNrGemeinde", "Sonntag").unique()
        parts.append(frame.join(days, on=["GeLfNr", "BfsNrGemeinde"], how="left").with_columns(year=pl.lit(year)))
    return pl.concat(parts, how="diagonal_relaxed")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Municipal parliaments of the canton of Zürich, their parties, and their electoral system from the "
        "Statistical Office of the canton of Zürich",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(CH)
    p.add(
        "source",
        id=_id("source", SOURCE),
        kind="official_body",
        publisher="Statistisches Amt des Kantons Zürich",
        title="Archiv Wahlresultate Gemeindeparlamentswahlen im Kanton Zürich",
        url="https://www.zh.ch/de/politik-staat/statistik-daten/datenkatalog.html",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Statistisches Amt des Kantons Zürich",
        notes="The data were entered by the municipalities and were not checked by the Statistical Office.",
    )
    p.add(
        "source",
        id=_id("source", SOURCE_CITY),
        kind="official_body",
        publisher="Stadt Zürich, Präsidialdepartement, Statistik Stadt Zürich",
        title="Gemeinderatswahlen: Resultate aller Kandidierenden und Herkunft der Stimmen",
        url="https://data.stadt-zuerich.ch/dataset/politik_gemeinderatswahlen_2022_resultate",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Statistik Stadt Zürich",
    )
    p.artifact(raw.cached(SOURCE, DOCUMENTATION), _id("source", SOURCE))
    for numbers in YEARS.values():
        for number in numbers:
            p.artifact(raw.cached(SOURCE, url(number)), _id("source", SOURCE))
    system = _id("electoral_system", "communal")
    p.add("electoral_system", id=system, polity_id=CH, valid_from=date(2000, 1, 1), rules=SYSTEM_RULES, rules_version=1)
    p.add("note", id=_id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    seats = _seats()
    for bfs, (name, _) in sorted(COMMUNES.items(), key=lambda item: int(item[0])):
        body = body_id(bfs)
        p.add("body", id=body, polity_id=CH, kind="council")
        p.name(body, f"{name} municipal parliament", "en", "official")
        p.name(body, f"Gemeindeparlament {name}", "de", "native", False)
        history = sorted((day, n) for (b, day), n in seats.items() if b == bfs)
        start = None
        for i, (day, n) in enumerate(history):
            if i and history[i - 1][1] == n:
                continue
            later = next((d for d, m in history[i + 1 :] if m != n), None)
            start = date.fromisoformat(day)
            p.add(
                "body_size",
                body_id=body,
                seats=n,
                valid_from=start,
                valid_to=date.fromisoformat(later) if later else None,
            )
    lists = _lists()
    names: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for row in lists.iter_rows(named=True):
        label = (row["ListBez"] or "").strip()
        code = (row["ListCode"] or "").strip()
        if not code:
            continue
        names[(str(row["BfsNrGemeinde"]), code)][label] = max(
            names[(str(row["BfsNrGemeinde"]), code)].get(label, 0), int(row["year"])
        )
    for day, year in CITY_DAYS.items():
        for code, label in city_lists(day).items():
            names[(ZURICH, code)][label] = max(names[(ZURICH, code)].get(label, 0), year)
    for (bfs, code), labels in sorted(names.items(), key=lambda item: (int(item[0][0]), item[0][1])):
        id = party_id(bfs, code)
        p.add("party", id=id, kind="party", polity_id=CH)
        ranked = sorted(labels.items(), key=lambda item: (-item[1], item[0]))
        for i, (label, _) in enumerate(ranked):
            if label:
                p.name(id, label, "de", "official" if i == 0 else "historical", i == 0)
        if code not in {label for label, _ in ranked}:
            p.name(id, code, "de", "abbreviation", False)
    return p


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _int(value: str | None) -> int | None:
    return None if value in (None, "") else int(Decimal(value))


def _year(value: str | None) -> int | None:
    found = re.fullmatch(r"\s*(1[89]\d\d|20\d\d)\s*", value or "")
    return int(found.group(1)) if found else None


def _title(day: date) -> tuple[str, str]:
    return (
        f"Municipal parliament elections in the canton of Zürich, {day.day} {day:%B %Y}",
        f"Gemeindeparlamentswahlen im Kanton Zürich, {day.day}. {MONTHS[day.month - 1]} {day.year}",
    )


CITY_LIST = re.compile(r"^Liste\s+(\d+)(?: – (.+?))?(?: – (.+))?$")


@functools.cache
def city_rows(day: str) -> list[dict[str, object]]:
    """The candidates of the city of Zürich's own results file: district, list, name, votes, and whether elected."""
    year = CITY_DAYS[day]
    frame = pl.read_csv(raw.cached(SOURCE, CITY.format(y=year)).path, infer_schema_length=0)
    frame = frame.filter(pl.col("Wahlkreis").is_not_null())
    labels: dict[int, dict[str, int]] = defaultdict(dict)
    rows: list[dict[str, object]] = []
    for row in frame.iter_rows(named=True):
        if year == 2010:
            text = re.sub(r"\s*–\s*", " – ", row["Liste"].strip())
            found = CITY_LIST.match(text)
            if found is None:
                raise ValueError(f"cannot read the list {text!r}")
            number, code, label = int(found.group(1)), found.group(2), found.group(3) or found.group(2)
            result = row["Wahlergebnis"]
        else:
            number, code, label = int(row["Liste_ID"]), row["Liste_Cd"], row["Liste_Bez_lang"]
            result = row["Wahlresultat"]
        labels[number][label] = labels[number].get(label, 0) + 1
        rows.append(
            {
                "district": wahlkreis(row["Wahlkreis"]),
                "number": number,
                "code": clean(code),
                "label": label,
                "last": clean(row["Nachname"]),
                "first": clean(row["Vorname"]),
                "votes": _int(row["total_stim"]),
                "elected": result == "gewählt",
            }
        )
    for row in rows:
        by = labels[int(str(row["number"]))]
        row["label"] = clean(max(by, key=lambda label: (by[label], label)))
    return rows


def city_lists(day: str) -> dict[str, str]:
    """The names of the lists of one city election by party code."""
    return {str(row["code"]): str(row["label"]) for row in city_rows(day)}


def city_election(day: str) -> Package:
    when = date.fromisoformat(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day} election of the city of Zürich's municipal parliament by district",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CH, ZURICH_AREA}
    p.uses(raw.cached(SOURCE, CITY.format(y=CITY_DAYS[day])))
    source = _id("source", SOURCE_CITY)
    p.artifact(raw.cached(SOURCE, CITY.format(y=CITY_DAYS[day])), source)
    english, german = _title(when)
    english = english.replace("in the canton of Zürich", "in the city of Zürich")
    german = german.replace("im Kanton Zürich", "in der Stadt Zürich")
    election_id = _id("election", day)
    p.add("election", id=election_id, polity_id=CH, kind="local", date_from=when, date_to=when, status="held")
    p.name(election_id, english, "en", "official")
    p.name(election_id, german, "de", "native", False)
    path = f"ch/{day}-local-zh"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contests: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in city_rows(day):
        contests[str(row["district"])].append(row)
    for code, rows in sorted(contests.items(), key=lambda item: int(item[0].split("+")[0])):
        title = code.replace("+", " + ")
        contest = _id("contest", day, ZURICH, code)
        district = district_area(code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body_id(ZURICH),
            district_area_id=district,
            magnitude=sum(1 for row in rows if row["elected"]),
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"Zürich municipal parliament, district {title}", "en", "official")
        p.name(contest, f"Gemeinderat Zürich, Wahlkreis {title}", "de", "native", False)
        p.add("slug", path=f"{path}/zurich-wahlkreis-{code.replace('+', '-')}", entity_id=contest, is_current=True)
        result_set = _id("result_set", day, ZURICH, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=_id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=CITY_METHOD + " " + ZURICH_NOTE,
            lang="en",
        )
        unit = _id("unit", day, ZURICH, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CH,
            scheme_code=COUNT_SCHEME,
            code=f"{day}:{ZURICH}:{code}",
            area_relation="equals",
            area_id=district,
            kind="standard",
        )
        options: dict[int, str] = {}
        for row in sorted(rows, key=lambda r: int(str(r["number"]))):
            number = int(str(row["number"]))
            if number in options:
                continue
            option = options[number] = _id("option", day, code, number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_id(ZURICH, str(row["code"])),
                ballot_position=number,
                ballot_label=str(row["label"]),
            )
            seats = sum(1 for r in rows if r["number"] == number and r["elected"])
            if seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
        for index, row in enumerate(rows):
            number = int(str(row["number"]))
            text = clean(f"{row['first']} {row['last']}")
            tag = f"{day}:{code}:{number}:{index}"
            option = _id("option", day, code, "candidate", index)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                parent_option_id=options[number],
                ballot_label=text,
            )
            person = _id("person", tag)
            p.add("person", id=person, birth_precision=None)
            p.name(person, text, "de", "official")
            candidacy = _id("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[number],
                role="list_member",
                ballot_name=text,
                member_of_party_id=party_id(ZURICH, str(row["code"])),
                is_independent=False,
            )
            if row["elected"]:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[number],
                    candidacy_id=candidacy,
                    kind="elected",
                )
            if row["votes"] is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=10,
                    count_round=0,
                    flags=0,
                    value=row["votes"],
                )
    return p


def election(day: str) -> Package:
    if day in CITY_DAYS:
        return city_election(day)
    when = date.fromisoformat(day)
    year = when.year
    candidate_number, list_number = YEARS[year][1], YEARS[year][2]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Municipal parliament elections in the canton of Zürich on {day} by counting district",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CH, ZURICH_AREA, *(area for _, area in COMMUNES.values())}
    for number in YEARS[year]:
        p.uses(raw.cached(SOURCE, url(number)))
    p.accept("accounting.votes_valid", VOTES_VALID if year < 2026 else VOTES_VALID_2026)
    source = _id("source", SOURCE)
    system = _id("electoral_system", "communal")
    english, german = _title(when)
    election_id = _id("election", day)
    p.add("election", id=election_id, polity_id=CH, kind="local", date_from=when, date_to=when, status="held")
    p.name(election_id, english, "en", "official")
    p.name(election_id, german, "de", "native", False)
    path = f"ch/{day}-local-zh"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    candidates = table(candidate_number).filter(pl.col("Sonntag") == day)
    candidates = candidates.with_columns(KNR=pl.col("KNR").str.strip_chars().str.replace_all(r"\D", ""))
    lists = table(list_number).filter(pl.col("GeLfNr").is_in(candidates["GeLfNr"].unique()))
    # Votes by list and counting district, with the ballots each list was named on where the file has them.
    ballots: dict[tuple[str, str, str], dict[int, int]] = {}
    for row in candidates.iter_rows(named=True):
        found = {m: _int(row.get(c)) for c, m in LIST_BALLOTS.items() if row.get(c) not in (None, "")}
        if found:
            ballots[(row["GeLfNr"], row["SortGemeindeSub"], row["ListeNr"])] = {
                m: v for m, v in found.items() if v is not None
            }
    for gelf in sorted(candidates["GeLfNr"].unique().to_list()):
        rows = candidates.filter(pl.col("GeLfNr") == gelf)
        head = rows.row(0, named=True)
        bfs = str(head["BfsNrGemeinde"])
        commune, commune_area = COMMUNES[bfs]
        in_zurich = bfs == ZURICH
        code = wahlkreis(head["WahlkreisBez"]) if in_zurich else None
        subs = rows.select("SortGemeindeSub", "Gemeinde").unique().sort("SortGemeindeSub")
        multiple = subs.height > 1 and not in_zurich
        contest = _id("contest", gelf)
        district = district_area(code) if code is not None else commune_area
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body_id(bfs),
            district_area_id=district,
            magnitude=int(head["Mandate"]),
            round_number=1,
            electoral_system_id=None if in_zurich else system,
            default_result_kind="counted",
        )
        title = f"{commune}, Wahlkreis {code.replace('+', ' + ')}" if code else commune
        p.name(
            contest,
            f"{title} municipal parliament"
            if not code
            else f"Zürich municipal parliament, district {code.replace('+', ' + ')}",
            "en",
            "official",
        )
        p.name(contest, f"Gemeinderat {title}" if code else f"Gemeindeparlament {commune}", "de", "native", False)
        p.add(
            "slug",
            path=f"{path}/{changeset.slug(commune)}" + (f"-wahlkreis-{code.replace('+', '-')}" if code else ""),
            entity_id=contest,
            is_current=True,
        )
        result_set = _id("result_set", gelf)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=_id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + (" " + ZURICH_NOTE if in_zurich else ""),
            lang="en",
        )
        # Reporting units: one per counting district.
        units: dict[str, str] = {}
        for sub, name in subs.iter_rows():
            unit = units[sub] = _id("unit", day, bfs, gelf if in_zurich else "", sub)
            code_text = f"{day}:{bfs}:{sub}" + (f":{code}" if in_zurich else "")
            if in_zurich:
                relation, area = "equals", district
            elif multiple:
                relation, area = "within", commune_area
            else:
                relation, area = "equals", commune_area
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CH,
                scheme_code=COUNT_SCHEME,
                code=code_text,
                area_relation=relation,
                area_id=area,
                kind="standard",
            )
        # Lists.
        contest_lists = lists.filter(pl.col("GeLfNr") == gelf)
        options: dict[str, str] = {}
        parties: dict[str, str] = {}
        alliances: dict[str, str] = {}
        for row in contest_lists.unique("ListNr").sort("ListNr").iter_rows(named=True):
            number = row["ListNr"]
            option = options[number] = _id("option", gelf, number)
            party = parties[number] = party_id(bfs, clean(row["ListCode"]))
            verbund = row.get("ListVerb")
            alliance = None
            if verbund:
                alliance = alliances.get(verbund)
                if alliance is None:
                    alliance = alliances[verbund] = _id("alliance", gelf, verbund)
                    p.add("alliance", id=alliance, kind="apparentement", election_id=election_id)
                    p.name(alliance, f"Listenverbindung {verbund}, {title}", "de", "native")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party,
                alliance_id=alliance,
                ballot_position=int(number),
                ballot_label=clean(row["ListBez"]),
            )
            if alliance:
                p.add("alliance_member", alliance_id=alliance, party_id=party)
            seats = _int(row.get("Sitze"))
            if seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
            declared = _int(row.get("StimmenTotalWahlKreis"))
            if declared is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=declared)
            extra = _int(row.get("StimmenZusatzWahlKreis"))
            if extra is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=9, value=extra)
        # Unit accounting, from the first candidate row of each counting district.
        for sub, unit in units.items():
            first = rows.filter(pl.col("SortGemeindeSub") == sub).row(0, named=True)
            figures = {measure: _int(first.get(column)) for column, measure in UNIT_MEASURES.items()}
            inserted = _int(first.get("StmEingelegtUngueltig"))
            if inserted is not None:
                figures[5] = (figures[5] or 0) + inserted
            for measure, value in figures.items():
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        for row in contest_lists.iter_rows(named=True):
            unit = units[row["SortGemeindeSub"]]
            option = options[row["ListNr"]]
            for column, measure in (("StimmenTotalAuszKreis", 1), ("StimmenZusatzAuszKreis", 9)):
                value = _int(row.get(column))
                if value is not None:
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
            for measure, value in ballots.get((gelf, row["SortGemeindeSub"], row["ListNr"]), {}).items():
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
        # Candidates. A candidate cumulated twice has two rows in each counting district.
        seen: set[str] = set()
        positions = {
            knr: n
            for knr, n in rows.group_by("SortGemeindeSub", "KNR")
            .len()
            .group_by("KNR")
            .agg(pl.col("len").max())
            .iter_rows()
        }
        rows = rows.unique(subset=["SortGemeindeSub", "KNR"], keep="first", maintain_order=True)
        for row in rows.iter_rows(named=True):
            knr = row["KNR"]
            number = row["ListeNr"]
            if knr not in seen:
                seen.add(knr)
                tag = f"{gelf}:{knr}"
                option = _id("option", gelf, knr)
                text = clean(f"{row['Vorname']} {row['Nachname']}")
                lines = positions[knr]
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=options[number],
                    ballot_position=int(knr[-2:]),
                    ballot_label=text,
                )
                person = _id("person", tag)
                birth = _year(row.get("Jahrgang"))
                p.add(
                    "person",
                    id=person,
                    birth_date=date(birth, 1, 1) if birth else None,
                    birth_precision="year" if birth else None,
                    gender={"M": "male", "W": "female"}.get(row.get("Geschlecht") or ""),
                )
                p.name(person, text, "de", "official")
                candidacy = _id("candidacy", tag)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=options[number],
                    role="list_member",
                    list_position=int(knr[-2:]),
                    ballot_name=text,
                    member_of_party_id=parties[number],
                    is_independent=False,
                    is_incumbent=True if row.get("Bisher") == "1" else (False if row.get("Bisher") == "0" else None),
                    ballot_lines=lines if lines > 1 else None,
                )
                if row.get("Gewaehlt") == "1":
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[number],
                        candidacy_id=candidacy,
                        kind="elected",
                    )
                total = _int(row.get("StimmenWahlkreis"))
                if total is not None:
                    p.add("declared", result_set_id=result_set, option_id=option, measure=10, value=total)
            value = _int(row.get("StimmenAuszKreis"))
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=units[row["SortGemeindeSub"]],
                    option_id=_id("option", gelf, knr),
                    channel=0,
                    measure=10,
                    count_round=0,
                    flags=0,
                    value=value,
                )
    return p
