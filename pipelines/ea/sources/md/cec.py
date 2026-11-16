import re
from datetime import date
from uuid import uuid5

import fastexcel

from ea import package, raw
from ea.package import Package

SOURCE = "md.cec"
BASE = "https://a.cec.md/index.php/ro/upload"
MOLDOVA = str(uuid5(package.NAMESPACE, "polity\x1fMD"))
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
PAGE = "https://a.cec.md/index.php/ro/alegeri-si-referendumuri-2830.html"

URLS = {
    "ps2014": f"{BASE}/2/89886/attached_files",
    "ps2014-districts": f"{BASE}/1/89886/attached_files",
    "ps2014-totals": f"{BASE}/3/89886/attached_files",
    "ps2021": f"{BASE}/620f7ee327cca/99908/attached_files",
    "pres2016-1": f"{BASE}/1/90358/attached_files",
    "pres2016-2": f"{BASE}/1/90359/attached_files",
    "pres2024-1": f"{BASE}/6858ff67671b8/113995/attached_files",
    "pres2024-2": f"{BASE}/68590e5edd817/113998/attached_files",
    "ref2024": f"{BASE}/67aee6476d633/111800/attached_files",
    "ref2019-1": "http://a.cec.md/storage/ckfinder/files/Procese%20verbale/Ref%202/Rezultate_Referendum_%C3%8Entrebarea_1.xlsx",
    "ref2019-2": "http://a.cec.md/storage/ckfinder/files/Procese%20verbale/Procese%20verbale%20uninominal/Rezultate_Referendum_%C3%8Entrebarea_2.xlsx",
    "ps2025": f"{BASE}/68e657b72b3ec/115495/attached_files",
}
SUFFIX = {"ps2014": ".xls", "ps2014-districts": ".xls", "ps2014-totals": ".xls"}
DAYS = {
    "2014-11-30": date(2014, 11, 30),
    "2016-10-30": date(2016, 10, 30),
    "2016-11-13": date(2016, 11, 13),
    "2019-02-24": date(2019, 2, 24),
    "2021-07-11": date(2021, 7, 11),
    "2024-10-20": date(2024, 10, 20),
    "2024-11-03": date(2024, 11, 3),
    "2025-09-28": date(2025, 9, 28),
}


EARLIER = {"2016-11-13": ["md.cec/2016-10-30"], "2024-11-03": ["md.cec/2024-10-20"]}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC_SOURCE = key("source", "cec")
PARLIAMENT = key("body", "parliament")
PRESIDENCY = key("office", "president")
SYSTEM_LIST = key("electoral_system", "parliament", "list")
SYSTEM_PRESIDENT = key("electoral_system", "president", "two_round")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")

METHOD = (
    "Counted per polling station and published by the Central Electoral Commission in its results workbook. "
    "Registered voters are the voters on the basic roll plus those on the supplementary roll. Voters are the voters "
    "who received a ballot, ballots cast are the voters who took part, and invalid ballots are those declared "
    "invalid. Polling stations abroad and on the left bank of the Dniester are listed in circumscriptions of their "
    "own."
)


class Station:
    def __init__(self, circumscription: int, code: str, place: str, name: str, row: list[int | None]) -> None:
        self.circumscription = circumscription
        self.code = code
        self.place = place
        self.name = name
        self.row = row


class Table:
    """A workbook's polling stations, its printed national row, and its option labels, in the sheet's own order."""

    def __init__(
        self,
        stations: list[Station],
        total: list[int | None],
        labels: list[str],
        tags: list[str],
        accounting: tuple[int, ...],
        first: int,
        end: int,
        named: bool = False,
    ) -> None:
        self.named = named  # whether the tags are party names rather than abbreviations
        self.stations = stations
        self.total = total
        self.labels = labels
        self.tags = tags
        self.accounting = accounting  # indexes of registered base, supplementary, voters, cast, invalid, valid
        self.first = first  # index of the first option
        self.end = end  # index after the last option


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def number(value: object) -> int | None:
    text = clean(value).replace(" ", "")
    if text in ("", "None", "-"):
        return None
    return int(float(text))


def sheet(url: str, name: str | None = None) -> list[list[str | None]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet_by_name(name or book.sheet_names[0], header_row=None, dtypes="string").to_polars()
    return [[None if cell is None else str(cell) for cell in row] for row in frame.rows()]


def _district(text: str) -> int | None:
    found = re.match(r"\s*(\d+)\s*-\s*Circumscrip", text) or re.search(r"nr\.\s*(\d+)\s*$", text)
    return int(found.group(1)) if found else None


def _row(row: list[str | None], first: int, end: int) -> list[int | None]:
    return [number(cell) for cell in row[first:end]]


def read_2021() -> Table:
    rows = sheet(URLS["ps2021"])
    width = len(rows[3])
    stations: list[Station] = []
    for row in rows[4:]:
        if row[2] is not None and re.fullmatch(r"\d+", clean(row[0])) and row[3] is not None:
            district = int(clean(row[0]))
            stations.append(
                Station(district, f"{district}/{int(clean(row[2]))}", clean(row[1]), clean(row[3]), _row(row, 4, width))
            )
    labels = [clean(cell) for cell in rows[3][10 : width - 3]]
    return Table(
        stations, _row(rows[4], 4, width), labels, [""] * len(labels), (0, 1, 2, 3, 5, width - 7), 6, width - 7
    )


def read_2024(url: str) -> Table:
    rows = sheet(url)
    width = len(rows[0])
    stations: list[Station] = []
    district = 0
    tags: list[str] = []
    for row in rows[1:]:
        text = clean(row[0])
        if "Circumscrip" in text and row[2] is None:
            district = _district(text) or 0
        elif row[0] is None and row[2] is None and not tags and any(row[9:]):
            tags = [clean(cell) for cell in row[9 : width - 3]]
        elif row[1] is not None and row[2] is not None and re.fullmatch(r"\d+", clean(row[1])):
            stations.append(
                Station(district, f"{district}/{int(clean(row[1]))}", text, clean(row[2]), _row(row, 3, width))
            )
    labels = [clean(cell) for cell in rows[0][9 : width - 3]]
    count = width - 3 - 9
    return Table(stations, _row(rows[1], 3, width), labels, tags, (0, 1, 2, 3, 5, 6 + count), 6, 6 + count)


def read_2025() -> Table:
    rows = sheet(URLS["ps2025"])
    width = max(i for i, cell in enumerate(rows[1]) if cell is not None) + 1
    stations: list[Station] = []
    district = 0
    for row in rows[3:]:
        text = clean(row[0])
        if row[2] is None and "Circumscrip" in text:
            district = _district(text) or 0
        elif re.fullmatch(r"\d+/\d+", text) and row[2] is not None:
            stations.append(Station(district, text, "", clean(row[1]), _row(row, 2, width)))
    labels = [clean(cell) for cell in rows[0][11:width]]
    return Table(stations, _row(rows[2], 2, width), labels, [""] * len(labels), (0, 1, 2, 3, 5, 6), 9, width - 2)


def read_2014() -> Table:
    rows = sheet(URLS["ps2014"], "Лист1")
    stations: list[Station] = []
    district = 0
    for row in rows[2:]:
        text = clean(row[0])
        if text.upper().startswith("CIRCUMSCRIP"):
            district = _district(text) or 0
        elif (
            row[1] is not None
            and clean(row[1]) != "Total"
            and re.fullmatch(r"\d+", clean(row[2]) if row[2] else "")
            and (re.fullmatch(r"\d+", text) or not text)
        ):
            numbered = int(text) if text else f"u{len(stations)}"
            stations.append(Station(district, f"{district}/{numbered}", clean(row[1]), "", _row(row, 2, 36)))
    by_district = sheet(URLS["ps2014-totals"], "Лист1")
    total = _row(next(row for row in by_district if clean(row[1]) == "TOTAL"), 2, 36)
    labels = [clean(cell) for cell in rows[1][8:33]]
    return Table(stations, total, labels, [""] * len(labels), (0, 1, 2, 3, 5, 6 + 25), 6, 6 + 25)


def read_2016(url: str) -> Table:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet_by_name(book.sheet_names[0], header_row=None, dtypes="string").to_polars()
    main = [[None if cell is None else str(cell) for cell in row] for row in frame.rows()]
    width = len(main[2])
    districts = [int(clean(row[0])) for row in main[4:] if re.fullmatch(r"\d+", clean(row[0]))]
    stations: list[Station] = []
    for district, name in zip(districts, book.sheet_names[1:], strict=True):
        rows = sheet(url, name)
        for row in rows[4:]:
            if clean(row[1]).upper().startswith("TOTAL"):
                continue
            if re.fullmatch(r"\d+", clean(row[0])) and row[2] is not None:
                stations.append(
                    Station(district, f"{district}/{int(clean(row[0]))}", clean(row[1]), "", _row(row, 2, width))
                )
    names, tags = [], []
    for cell in main[2][8 : width - 3]:
        name, _, party = clean(cell).partition(", ")
        names.append(name)
        tags.append("" if party.lower().startswith("candidat independent") else party)
    total = _row(next(row for row in main if clean(row[1]) == "TOTAL"), 2, width)
    count = width - 3 - 8
    return Table(stations, total, names, tags, (0, 1, 2, 3, 5, 6 + count), 6, 6 + count, named=True)


def read_2019(url: str) -> Table:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    names = {
        int(clean(row[0])): clean(row[1]) for row in sheet(url, "TOTAL")[4:] if re.fullmatch(r"\d+", clean(row[0]))
    }
    stations: list[Station] = []
    for name in book.sheet_names[1:]:
        for row in sheet(url, name)[4:]:
            if re.fullmatch(r"\d+", clean(row[0])):
                district = int(name)
                stations.append(
                    Station(district, f"{district}/{int(clean(row[0]))}", names[district], "", _row(row, 1, 12))
                )
    rows = sheet(url, "TOTAL")
    total = _row(next(row for row in rows if clean(row[0]) == "Total"), 2, 13)
    labels = [clean(cell) for cell in rows[2][13:] if cell] or [clean(cell) for cell in sheet(url, "1")[2][10:12]]
    return Table(stations, total, labels, [""] * len(labels), (0, 1, 2, 3, 5, 6), 9, 11)


def table(day: str, which: str = "") -> Table:
    if which.startswith("ref2019"):
        return read_2019(URLS[which])
    if which.startswith("pres2016"):
        return read_2016(URLS[which])
    if day == "2014-11-30":
        return read_2014()
    if day == "2021-07-11":
        return read_2021()
    if day == "2025-09-28":
        return read_2025()
    return read_2024(URLS[which])


def packages() -> list[str]:
    return ["md.cec/registry", *(f"md.cec/{day}" for day in DAYS)]


def fetch() -> None:
    for name, url in URLS.items():
        raw.fetch(SOURCE, url, suffix=SUFFIX.get(name, ".xlsx"))


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def party_key(label: str) -> str:
    text = re.sub(r"[„”“\"']", "", label).strip()
    text = re.sub(r"^(partidul politic|partid politic|partidul)\s+", "", text, flags=re.IGNORECASE)
    return clean(text).casefold()


def party_id(label: str) -> str:
    return key("party", party_key(label))


def tag_party(tag: str) -> str:
    return key("party", "tag", tag)


def list_labels() -> dict[str, str]:
    """Every list's printed name across the parliamentary elections, by party id."""
    found: dict[str, str] = {}
    for day in ("2014-11-30", "2021-07-11", "2025-09-28"):
        for label in table(day).labels:
            if "Candidat independent" not in label:
                found.setdefault(party_id(label), re.sub(r"\s+", " ", label.strip()))
    return found


def candidate_tags() -> set[str]:
    tags: set[str] = set()
    for which in ("pres2024-1", "pres2024-2"):
        tags |= {tag for tag in table("2024-10-20", which).tags if tag and not tag.startswith("ci-")}
    return tags


def candidate_parties() -> dict[str, str]:
    found: dict[str, str] = {}
    for which in ("pres2016-1", "pres2016-2"):
        for tag in table("2016-10-30", which).tags:
            if tag:
                found.setdefault(party_id(tag), tag)
    return found


def registry() -> Package:
    p = Package(
        "md.cec/registry",
        SOURCE,
        "Moldovan parties, the Parliament, the presidency, and their electoral systems, from the Central Electoral "
        "Commission's results workbooks",
    )
    p.external.add(MOLDOVA)
    p.add(
        "source",
        id=CEC_SOURCE,
        kind="official_body",
        publisher="Comisia Electorală Centrală a Republicii Moldova",
        title="Election results workbooks published by the Central Electoral Commission",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Comisia Electorală Centrală a Republicii Moldova",
    )
    for url in URLS.values():
        p.artifact(raw.cached(SOURCE, url), CEC_SOURCE)
    p.add(
        "scheme",
        code="md.circumscription",
        polity_id=MOLDOVA,
        kind="electoral_district",
        rank=1,
        noun="circumscription",
    )
    p.add("scheme", code="md.sv", polity_id=MOLDOVA, kind="precinct", rank=2, noun="polling station")
    p.add("body", id=PARLIAMENT, polity_id=MOLDOVA, kind="unicameral")
    p.name(PARLIAMENT, "Parliament of Moldova", "en", "official")
    p.name(PARLIAMENT, "Parlamentul Republicii Moldova", "ro", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=MOLDOVA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Moldova", "en", "official")
    p.name(PRESIDENCY, "Președintele Republicii Moldova", "ro", "native", False)
    systems = (
        (
            SYSTEM_LIST,
            {"formula": "dhondt"},
            (
                "A single national constituency elects 101 deputies by party-list proportional representation. The "
                "electoral thresholds are not stored here."
            ),
        ),
        (
            SYSTEM_PRESIDENT,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate with a majority of valid votes in the first round is elected; otherwise the two leading "
                "candidates go to a second round."
            ),
        ),
        (SYSTEM_REFERENDUM, {"formula": "referendum"}, "A yes or no question put to all voters."),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=MOLDOVA, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for id, text in sorted({**candidate_parties(), **list_labels()}.items(), key=lambda item: item[1]):
        p.add("party", id=id, kind="bloc" if text.lower().startswith("blocul") else "party", polity_id=MOLDOVA)
        p.name(id, text, "ro", "official")
    for tag in sorted(candidate_tags()):
        id = tag_party(tag)
        p.add("party", id=id, kind="party", polity_id=MOLDOVA)
        p.name(id, tag, "ro", "abbreviation")
    return p


def _units(p: Package, day: str, tables: list[Table]) -> dict[str, str]:
    units: dict[str, str] = {}
    held = {str(row["code"]) for row in p.rows.get("reporting_unit", [])}
    for stations in (t.stations for t in tables):
        for station in stations:
            code = f"{day}:{station.code}"
            unit = units[station.code] = key("unit", code)
            if code in held:
                continue
            held.add(code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=MOLDOVA,
                scheme_code="md.sv",
                code=code,
                area_relation="unlocated",
                area_id=None,
                kind="standard",
            )
            text = ", ".join(part for part in (station.place, station.name) if part)
            if text:
                p.name(
                    unit,
                    f"{text} (circumscription {station.circumscription}, station {station.code.split('/')[1]})",
                    "ro",
                    "official",
                )
    return units


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def _declared(p: Package, result_set: str, options: list[str], t: Table) -> None:
    a, b, c, d, f, h = (t.total[i] for i in t.accounting)
    registered = None if a is None or b is None else a + b
    for measure, value in ((REGISTERED, registered), (VOTERS, c), (CAST, d), (INVALID, f), (VALID, h)):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for option, value in zip(options, t.total[t.first : t.end], strict=True):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)


def _accounting(p: Package, result_set: str, units: dict[str, str], t: Table) -> None:
    for station in t.stations:
        a, b, c, d, f, h = (station.row[i] for i in t.accounting)
        registered = None if a is None or b is None else a + b
        for measure, value in ((REGISTERED, registered), (VOTERS, c), (CAST, d), (INVALID, f), (VALID, h)):
            _put(p, result_set, units[station.code], None, measure, value)


def _result_set(p: Package, election: str, contest: str, name: str) -> str:
    result_set = key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CEC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    return result_set


def _election(p: Package, name: str, day: date, kind: str, english: str, romanian: str, path: str) -> str:
    election = key("election", name)
    p.add("election", id=election, polity_id=MOLDOVA, kind=kind, date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, romanian, "ro", "native", False)
    return election


def _contest(
    p: Package, name: str, election: str, kind: str, english: str, romanian: str, path: str, **fields: object
) -> str:
    contest = key("contest", name)
    row = {"round_number": 1, "default_result_kind": "counted", **fields}
    p.add("contest", id=contest, election_id=election, kind=kind, **row)
    p.name(contest, english, "en", "official")
    p.name(contest, romanian, "ro", "native", False)
    p.add("slug", path=path, entity_id=contest, is_current=True)
    return contest


def build(day: str) -> Package:
    first = DAYS[day]
    p = Package(
        f"md.cec/{day}",
        SOURCE,
        f"The Moldovan elections of {first} by polling station, from the Central Electoral Commission's workbooks",
        depends=["md.cec/registry", *EARLIER.get(day, [])],
    )
    p.external.add(MOLDOVA)
    if day in ("2014-11-30", "2021-07-11", "2025-09-28"):
        _parliament(p, day, first)
    elif day == "2019-02-24":
        _referendum(p, day, first)
    elif day == "2024-10-20":
        _presidential(p, day, first, 1)
        _referendum(p, day, first)
    else:
        _presidential(p, day, first, 2 if day in EARLIER else 1)
    return p


def _uses(p: Package, *names: str) -> None:
    for name in names:
        p.uses(raw.cached(SOURCE, URLS[name]))


def _parliament(p: Package, day: str, first: date) -> None:
    year = first.year
    kind = "general"
    name = {"2014-11-30": "ps2014", "2021-07-11": "ps2021", "2025-09-28": "ps2025"}[day]
    _uses(p, name, *(("ps2014-totals",) if year == 2014 else ()))
    snap = "early " if year == 2021 else ""
    election = _election(
        p,
        f"ps{day}",
        first,
        kind,
        f"{first.year} Moldovan {snap}parliamentary election",
        f"Alegeri parlamentare {'anticipate ' if year == 2021 else ''}din {first.day}.{first.month:02d}.{first.year}",
        f"md/{day}-general",
    )
    contest = _contest(
        p,
        f"ps{day}",
        election,
        "list_allocation",
        f"{first.year} Moldovan {snap}parliamentary election",
        f"Alegeri parlamentare {first.year}",
        f"md/{day}-general/parliament",
        body_id=PARLIAMENT,
        magnitude=101,
        electoral_system_id=SYSTEM_LIST,
    )
    t = table(day)
    result_set = _result_set(p, election, contest, f"ps{day}")
    options: list[str] = []
    for position, (label, tag) in enumerate(zip(t.labels, t.tags, strict=True), 1):
        option = key("option", f"ps{day}", position)
        options.append(option)
        independent = "Candidat independent" in label or label.lower().startswith("candidat independent")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate" if independent else "list",
            party_id=None if independent else party_id(label),
            ballot_position=position,
            ballot_label=label,
        )
    units = _units(p, day, [t])
    _accounting(p, result_set, units, t)
    for station in t.stations:
        for option, value in zip(options, station.row[t.first : t.end], strict=True):
            _put(p, result_set, units[station.code], option, VOTES, value)
    _declared(p, result_set, options, t)
    _caveats(p, result_set, t)
    if day == "2025-09-28":
        p.accept(
            "accounting.declared",
            "the printed national row differs from the sum of its own polling-station rows by one in invalid "
            "ballots (30,857 against 30,856), valid votes (1,578,722 against 1,578,723), and one list's votes (0 "
            "against 1); stored as printed per station",
        )


def _caveats(p: Package, result_set: str, t: Table) -> None:
    over = 0
    for station in t.stations:
        a, b, _, d, _, _ = (station.row[i] for i in t.accounting)
        if a is not None and b is not None and d is not None and d > a + b:
            over += 1
    if over:
        p.accept(
            "accounting.turnout",
            f"{over} polling stations print more ballots cast than voters on both rolls; stored as printed",
        )
        p.add(
            "note",
            id=key("note", result_set, "turnout"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"At {over} polling stations, more ballots were cast than voters on the basic and supplementary "
            "rolls together. The figures are stored as printed.",
            lang="en",
        )


def _candidates(
    p: Package, name: str, year: int, round_number: int, contest: str, t: Table, build_people: bool
) -> list[str]:
    options: list[str] = []
    for position, (label, tag) in enumerate(zip(t.labels, t.tags, strict=True), 1):
        person = key("person", f"pres{year}", label)
        if build_people:
            p.add("person", id=person)
            p.name(person, label, "ro", "official")
        option = key("option", f"{name}-{round_number}", position)
        options.append(option)
        independent = tag.startswith("ci-") or not tag
        party = None if independent else (party_id(tag) if t.named else tag_party(tag))
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=label,
        )
        p.add(
            "candidacy",
            id=key("candidacy", f"{name}-{round_number}", position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=label,
            member_of_party_id=party,
            is_independent=independent,
        )
    return options


def _presidential(p: Package, day: str, first: date, round_number: int) -> None:
    year = first.year
    name = f"pres{year}"
    which = f"{name}-{round_number}"
    _uses(p, which)
    runoff = round_number == 2
    election = _election(
        p,
        which,
        first,
        "runoff" if runoff else "general",
        f"{year} Moldovan presidential election" + (", second round" if runoff else ""),
        f"Alegeri prezidențiale {year}" + (", turul II" if runoff else ""),
        f"md/{day}-presidential" + ("-runoff" if runoff else ""),
    )
    contest = _contest(
        p,
        which,
        election,
        "office",
        f"{year} Moldovan presidential election, {'second' if runoff else 'first'} round",
        f"Alegeri prezidențiale {year}, turul {'II' if runoff else 'I'}",
        f"md/{day}-presidential{'-runoff' if runoff else ''}/president",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=round_number,
        previous_round_id=key("contest", f"{name}-1") if runoff else None,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    t = table(day, which)
    result_set = _result_set(p, election, contest, which)
    options = _candidates(p, name, year, round_number, contest, t, not runoff)
    units = _units(p, day, [t])
    _accounting(p, result_set, units, t)
    for station in t.stations:
        for option, value in zip(options, station.row[t.first : t.end], strict=True):
            _put(p, result_set, units[station.code], option, VOTES, value)
    _declared(p, result_set, options, t)
    _caveats(p, result_set, t)
    if year == 2016:
        p.accept(
            "accounting.declared",
            "the Hîncești sheet of the workbook omits polling stations 29 and 30, while the circumscription and "
            "national totals it prints include them, so the stored station rows fall short of the printed totals",
        )
        p.add(
            "note",
            id=key("note", result_set, "omitted"),
            entity_id=result_set,
            kind="caveat",
            body_md="The published workbook lists no results for polling stations 29 and 30 of the Hîncești "
            "circumscription, although its circumscription and national totals count them. Those two stations are "
            "missing here.",
            lang="en",
        )


def _referendum(p: Package, day: str, first: date) -> None:
    year = first.year
    election = _election(
        p,
        f"ref{year}",
        first,
        "referendum",
        f"{year} Moldovan {'constitutional ' if year == 2024 else 'republican '}referendum",
        f"Referendum republican {'constituțional ' if year == 2024 else ''}{year}",
        f"md/{day}-referendum",
    )
    questions = ("ref2024",) if year == 2024 else ("ref2019-1", "ref2019-2")
    units: dict[str, str] = {}
    for index, which in enumerate(questions, 1):
        _uses(p, which)
        suffix = f", question {index}" if len(questions) > 1 else ""
        contest = _contest(
            p,
            which,
            election,
            "ballot_question",
            f"{year} Moldovan referendum{suffix}",
            f"Referendum republican {year}" + (f", întrebarea {index}" if len(questions) > 1 else ""),
            f"md/{day}-referendum/question" + (f"-{index}" if len(questions) > 1 else ""),
            electoral_system_id=SYSTEM_REFERENDUM,
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        t = table(day, which)
        result_set = _result_set(p, election, contest, which)
        options: list[str] = []
        for position, label in enumerate(t.labels, 1):
            option = key("option", which, position)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        units = _units(p, day, [t])
        _accounting(p, result_set, units, t)
        for station in t.stations:
            for option, value in zip(options, station.row[t.first : t.end], strict=True):
                _put(p, result_set, units[station.code], option, VOTES, value)
        _declared(p, result_set, options, t)
        _caveats(p, result_set, t)
