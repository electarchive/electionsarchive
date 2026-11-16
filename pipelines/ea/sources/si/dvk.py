import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "si.dvk"
BASE = "https://www.dvk-rs.si/fileadmin/user_upload/dokumenti/volitve"
SLOVENIA = str(uuid5(package.NAMESPACE, "polity\x1fSI"))
ELECTORAL_UNITS = range(1, 9)
DISTRICTS = range(1, 12)


@dataclass(frozen=True)
class Vote:
    """One published polling-station workbook."""

    kind: str
    day: date
    path: str
    sheet: str
    title: str
    native: str
    round: int = 1
    note: str = ""

    @property
    def url(self) -> str:
        return f"{BASE}/{self.path}"


VOTES = [
    Vote(
        "dz",
        date(2011, 12, 4),
        "drzavni_zbor_2011/Voltive-DZ2011-koncni-izid-po-voliscih.xls",
        "Celota",
        "2011 Slovenian parliamentary election",
        "Predčasne volitve v Državni zbor 2011",
    ),
    Vote(
        "dz",
        date(2014, 7, 13),
        "drzavni_zbor_2014/VsaVolisca20140721-_12_.xlsx",
        "Vsa volišča",
        "2014 Slovenian parliamentary election",
        "Predčasne volitve v Državni zbor 2014",
    ),
    Vote(
        "dz",
        date(2018, 6, 3),
        "drzavni_zbor_2018/Koncni-izid_VsaVolisca20180612Internet.xlsx",
        "Vsa volišča internet",
        "2018 Slovenian parliamentary election",
        "Predčasne volitve v Državni zbor 2018",
    ),
    Vote(
        "dz",
        date(2022, 4, 24),
        "DZ/2022/izidi_volitev/VoliscaDZ20220506Koncni.xlsx",
        "Vsi",
        "2022 Slovenian parliamentary election",
        "Volitve v Državni zbor 2022",
    ),
    Vote(
        "pres",
        date(2017, 10, 22),
        "predsednika_2017/Koncni-izid_volitev-predsednika_prvi_krog_stanje_2.11.2017.xlsx",
        "vsi",
        "2017 Slovenian presidential election, first round",
        "Volitve predsednika republike 2017, 1. krog",
    ),
    Vote(
        "pres",
        date(2017, 11, 12),
        "predsednika_2017/Koncni_izid_drgu_krog_21.11.2017.xlsx",
        "998",
        "2017 Slovenian presidential election, second round",
        "Volitve predsednika republike 2017, 2. krog",
        round=2,
    ),
    Vote(
        "ep",
        date(2014, 5, 25),
        "evropski_parlament_2014/volitve-v-ep-2014-izzidi-glasovanja-po-voliscih.xlsx",
        "Vsi",
        "2014 European Parliament election in Slovenia",
        "Volitve poslancev iz Republike Slovenije v Evropski parlament 2014",
    ),
    Vote(
        "ref",
        date(2012, 3, 25),
        "Referendum-druzinski-zakonik-koncni-izid-po-voliscih.xls",
        "Po voliščih 3.4.2012",
        "2012 Slovenian referendum on the Family Code",
        "Zakonodajni referendum o Družinskem zakoniku 2012",
        note="Family Code (Družinski zakonik)",
    ),
    Vote(
        "ref",
        date(2014, 6, 8),
        "referendum_2014/Izidi-referenduma_AS2014Volisca20140616_koncni.xlsx",
        "Sheet1",
        "2014 Slovenian referendum on the Archives Act",
        "Zakonodajni referendum o Zakonu o arhivskem gradivu in arhivih 2014",
        note="Archival Material and Archives Act (ZAGA)",
    ),
    Vote(
        "ref",
        date(2015, 12, 20),
        "referendum_2015/Izid_glasovanja_po_volis__c__ih_SLO_dan_glasovanja_20.12.2015_2_2016.xlsx",
        "Sheet2",
        "2015 Slovenian referendum on the Marriage and Family Relations Act amendment",
        "Zakonodajni referendum o Zakonu o spremembi Zakona o zakonski zvezi in družinskih razmerjih 2015",
        note="Act amending the Marriage and Family Relations Act (ZZZDR-D)",
    ),
    Vote(
        "ref",
        date(2017, 9, 24),
        "referendum_2017/Izid_glaosvanja_ZIUGDT_konc__ni_16.10.2017_za_poroc__ilo_DVK.xlsx",
        "Sheet1",
        "2017 Slovenian referendum on the Divača–Koper railway act",
        "Zakonodajni referendum o Zakonu o izvedbi investicije drugega tira železniške proge Divača–Koper 2017",
        note="Act on the second track of the Divača–Koper railway (ZIUGDT)",
    ),
    Vote(
        "ref",
        date(2018, 5, 13),
        "referendum_2018/Izid_po_volis__c__ih_SLO_na_dan_glasovanja_13.5.2018.xlsx",
        "Sheet1",
        "2018 Slovenian repeat referendum on the Divača–Koper railway act",
        "Ponovno glasovanje na referendumu o ZIUGDT 2018",
        note="Repeat vote on the Act on the second track of the Divača–Koper railway (ZIUGDT)",
    ),
    Vote(
        "ref",
        date(2021, 7, 11),
        "referendum_2021/Koncni-izid-glasovanja_po-voliscih.xlsx",
        "VSI",
        "2021 Slovenian referendum on the Waters Act amendment",
        "Zakonodajni referendum o Zakonu o spremembah in dopolnitvah Zakona o vodah (ZV-1G) 2021",
        note="Act amending the Waters Act (ZV-1G)",
    ),
]


def fetch() -> None:
    for vote in VOTES:
        raw.fetch(SOURCE, vote.url, suffix=vote.path.rsplit(".", 1)[1])


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def clean(text: str | None) -> str:
    return " ".join((text or "").replace("\xa0", " ").split())


def whole(text: str | None) -> int | None:
    if text is None or text.strip() in ("", "NULL"):
        return None
    return int(float(text.replace(",", ".")))


@dataclass
class Row:
    ve: str
    vo: str
    stv: str
    place: str
    ve_name: str
    vo_name: str
    registered: int | None
    voters: int | None
    cast: int | None
    invalid: int | None
    valid: int | None
    options: list[int | None]
    roll: int = 0
    certificates: int = 0


@dataclass
class Sheet:
    rows: list[Row]
    total: Row | None
    options: list[str]


def find(names: list[str], *candidates: str, prefix: bool = False) -> int | None:
    for candidate in candidates:
        for position, name in enumerate(names):
            if name == candidate or (prefix and name.startswith(candidate)):
                return position
    return None


def load(vote: Vote) -> Sheet:
    artifact = raw.cached(SOURCE, vote.url)
    book = fastexcel.read_excel(str(artifact.path))
    frame = book.load_sheet_by_name(vote.sheet, header_row=0, dtypes="string").to_polars()
    header = list(frame.columns)
    names = [plain(column) for column in header]
    ve, vo, stv, place = (
        find(names, "ve"),
        find(names, "vo", "ovk"),
        find(names, "stv", "stvol", "tvol"),
        find(names, "volisce", "volise"),
    )
    cast, invalid = find(names, "oddan", "oddane", prefix=True), find(names, "neveljav", prefix=True)
    if None in (ve, vo, stv, place, cast, invalid):
        raise LookupError(f"{vote.path} lacks a station column: {header[:16]}")
    registered = find(names, "skupajvolivcev", "volivcev")
    voters = find(names, "skupajglasovalo", "glasovalo")
    valid = find(names, "skupajveljavnih", "veljavnih")
    roll, certificates = find(names, "imenik"), find(names, "potrdila", "potrdilo")
    ballots = [find(names, name) for name in ("glimenik", "glpotrdila", "glinv")]
    ve_name, vo_name = find(names, "imeve", "enota"), find(names, "imevo", "imeovk", "okraj")
    if vote.kind == "ep":
        first = next(i for i, name in enumerate(names) if name[:1].isdigit())
    elif vote.kind == "ref":
        first = len(header) - 2
    else:
        first = max(i for i in (ve, vo, stv, place, registered, voters, cast, invalid, valid) if i is not None) + 1
    options = list(range(first, len(header)))

    def number(line: tuple[object, ...], position: int | None) -> int | None:
        text = None if position is None else line[position]
        return None if text is None or str(text).strip() in ("", "NULL") else int(float(str(text).replace(",", ".")))

    def text(line: tuple[object, ...], position: int | None) -> str:
        return "" if position is None or line[position] is None else clean(str(line[position]))

    rows: list[Row] = []
    total: Row | None = None
    for line in frame.rows():
        if text(line, ve).lower() == "ve":
            continue
        row = Row(
            ve=text(line, ve),
            vo=text(line, vo),
            stv=text(line, stv),
            place=text(line, place),
            ve_name=text(line, ve_name),
            vo_name=text(line, vo_name),
            registered=number(line, registered),
            voters=number(line, voters),
            cast=number(line, cast),
            invalid=number(line, invalid),
            valid=number(line, valid),
            options=[number(line, position) for position in options],
            roll=number(line, roll) or 0,
            certificates=number(line, certificates) or 0,
        )
        if vote.kind == "ep":
            row.registered = row.roll + row.certificates
            row.voters = sum(number(line, position) or 0 for position in ballots)
        if row.stv in ("", "None"):
            if any(cell not in (None, "") for cell in line):
                total = row
            continue
        rows.append(row)
    return Sheet(rows, total, [header[position] for position in options])


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DVK_SOURCE = key("source", SOURCE)
CHAMBER_BODY = key("body", "national-assembly")
PRESIDENCY = key("office", "president")
SYSTEMS = {
    "dz": key("electoral_system", "dz"),
    "pres": key("electoral_system", "president"),
    "ep": key("electoral_system", "ep"),
    "ref": key("electoral_system", "referendum"),
}
METHOD = {
    "dz": (
        "Counted by the polling station (volišče) and published by the State Election Commission (Državna volilna "
        "komisija). The 88 deputies chosen by list are elected in 8 electoral units of 11 deputies each; every "
        "electoral unit is a contest here. Registered voters are the electoral roll plus voters holding a voting "
        "certificate, ballots cast are the ballots found in the box, and valid votes are the valid list votes. "
        "Stations numbered 970 and above are not places: they hold postal votes, votes cast at any station (OMNIA), "
        "votes from diplomatic and consular missions, and early voting. They have no roll of their own, so no "
        "registered voters are stored for them. Preference votes for candidates are not published at this level. "
        "The two seats of the Italian and Hungarian national communities are elected separately and are not here."
    ),
    "pres": (
        "Counted by the polling station (volišče) and published by the State Election Commission (Državna volilna "
        "komisija). Registered voters are the electoral roll plus voters holding a voting certificate, ballots cast "
        "are the ballots found in the box, and valid votes are the valid votes for candidates. Stations numbered 970 "
        "and above are not places: they hold postal votes, votes cast at any station (OMNIA), and votes from "
        "diplomatic and consular missions. They have no roll of their own, so no registered voters are stored for "
        "them."
    ),
    "ep": (
        "Counted by the polling station (volišče) and published by the State Election Commission (Državna volilna "
        "komisija). Slovenia forms one electoral unit for the European Parliament. Registered voters are the "
        "electoral roll plus voters holding a voting certificate, voters are those who voted by roll, certificate, "
        "or as disabled voters, and ballots cast are the ballots found in the box. Each candidate's preference "
        "votes are stored under the candidate. Stations numbered 970 and above are not places: they hold postal "
        "votes, votes cast at any station, and votes from diplomatic and consular missions. They have no roll of "
        "their own, so no registered voters are stored for them."
    ),
    "ref": (
        "Counted by the polling station (volišče) and published by the State Election Commission (Državna volilna "
        "komisija). Registered voters are the electoral roll plus voters holding a voting certificate, ballots cast "
        "are the ballots found in the box, and valid votes are the valid ballots for or against. Stations numbered "
        "970 and above, and the diplomatic and consular missions, are not places: they hold postal votes and votes "
        "cast at any station (OMNIA). They have no roll of their own, so no registered voters are stored for them. "
        "A law is rejected at a legislative referendum when most valid votes are against it and the votes against "
        "are at least a fifth of all registered voters."
    ),
}
SYSTEM_RULES: dict[str, tuple[date, dict[str, object], str, str]] = {
    "dz": (
        date(2011, 1, 1),
        {
            "formula": "droop",
            "remainders": "national",
            "threshold": {"level": "list", "scope": "national", "percent": 4},
        },
        "National Assembly electoral system",
        "Volilni sistem Državnega zbora",
    ),
    "pres": (
        date(1992, 1, 1),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        "Presidential electoral system",
        "Volilni sistem predsednika republike",
    ),
    "ep": (
        date(2004, 5, 1),
        {"formula": "dhondt"},
        "European Parliament electoral system in Slovenia",
        "Volilni sistem za Evropski parlament",
    ),
    "ref": (
        date(1994, 1, 1),
        {"formula": "referendum"},
        "Legislative referendum",
        "Zakonodajni referendum",
    ),
}


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def title(text: str) -> str:
    return " ".join(word.capitalize() for word in clean(text).split(" "))


def district(ve: str, vo: str) -> tuple[int | None, int | None]:
    unit = int(ve)
    unit = unit // 1000 if unit >= 1000 else unit
    seat = int(vo)
    seat = seat % 1000 if seat >= 1000 else seat
    if unit not in ELECTORAL_UNITS:
        return None, None
    return unit, seat if seat in DISTRICTS else None


def station_kind(place: str) -> str:
    text = place.upper()
    if "POŠTA" in text:
        return "postal_district"
    if "DKP" in text or "AMBASADA" in text:
        return "abroad_mission"
    if "OMNIA" in text or "POSEBNI VOLILNI" in text or "PREDČASNO" in text:
        return "aggregate"
    return "standard"


def packages() -> list[str]:
    return ["si.dvk/geo", "si.dvk/registry", *(slug(vote) for vote in VOTES)]


def slug(vote: Vote) -> str:
    word = {"dz": "dz", "pres": "pres", "ep": "ep", "ref": "ref"}[vote.kind]
    return f"si.dvk/{word}-{vote.day}"


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()
    registry().write()
    sheets = {vote: load(vote) for vote in VOTES}
    report: dict[str, dict[str, object]] = {}
    for vote, sheet in sheets.items():
        report[slug(vote)] = reconcile(vote, sheet)
        election(vote, sheet).write()
    for slugged, found in report.items():
        print(f"{slugged}: totals {found}", flush=True)


def reconcile(vote: Vote, sheet: Sheet) -> dict[str, object]:
    """Compare the sum of the station rows with the national total row the commission prints."""
    total = sheet.total
    if total is None:
        raise ValueError(f"{vote.path} prints no total row")
    problems: list[str] = []
    for field_name in ("registered", "voters", "cast", "invalid", "valid"):
        got = sum(getattr(row, field_name) or 0 for row in sheet.rows)
        wanted = getattr(total, field_name)
        if wanted is not None and wanted != got:
            problems.append(f"{field_name}: stations {got:,}, printed {wanted:,}")
    for index, name in enumerate(sheet.options):
        got = sum(row.options[index] or 0 for row in sheet.rows)
        if total.options[index] != got:
            problems.append(f"{name}: stations {got:,}, printed {total.options[index]}")
    if problems:
        raise ValueError(f"{vote.path}: station rows disagree with the printed total: {problems}")
    return {
        "stations": len(sheet.rows),
        "registered": total.registered,
        "cast": total.cast,
        "valid": total.valid,
        "matches_printed": True,
    }


RAW_FILES = sorted({vote.url for vote in VOTES})
PREFERENCE: set[str] = set()


def geo() -> Package:
    p = Package(
        "si.dvk/geo",
        SOURCE,
        "Slovenia's 8 National Assembly electoral units and 88 electoral districts, as numbered by the State "
        "Election Commission",
    )
    p.external.add(SLOVENIA)
    for code, rank, kind, noun in (
        ("si.ve", 1, "electoral_district", "electoral unit"),
        ("si.vo", 2, "electoral_district", "electoral district"),
        ("si.volisce", 3, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=SLOVENIA, kind=kind, rank=rank, noun=noun, code_system=None)
    vote = next(v for v in VOTES if v.kind == "dz" and v.day.year == 2018)
    units: dict[int, str] = {}
    seats: dict[tuple[int, int], str] = {}
    for row in load(vote).rows:
        unit, seat = district(row.ve, row.vo)
        if unit is not None:
            units.setdefault(unit, row.ve_name)
            if seat is not None:
                seats.setdefault((unit, seat), row.vo_name)
    for unit in ELECTORAL_UNITS:
        id = area("si.ve", str(unit))
        p.add("area", id=id, scheme_code="si.ve")
        p.add("area_code", area_id=id, code=str(unit))
        p.name(id, title(units[unit]), "sl", "official")
    for (unit, seat), text in sorted(seats.items()):
        id = area("si.vo", f"{unit}.{seat}")
        p.add("area", id=id, scheme_code="si.vo")
        p.add("area_code", area_id=id, code=f"{unit}.{seat}")
        p.add("area_parent", area_id=id, parent_id=area("si.ve", str(unit)))
        p.name(id, title(text), "sl", "official")
    return p


def registry() -> Package:
    p = Package(
        "si.dvk/registry",
        SOURCE,
        "The State Election Commission as a source, the National Assembly, the presidency, and the electoral systems",
        depends=["si.dvk/geo"],
    )
    p.external.add(SLOVENIA)
    p.add(
        "source",
        id=DVK_SOURCE,
        kind="official_body",
        publisher="Državna volilna komisija",
        title="Results of elections and referendums by polling station",
        url="https://www.dvk-rs.si/arhiv-volitev/",
        license="Public information of the State Election Commission",
        license_url="https://www.dvk-rs.si/informacije-javnega-znacaja/",
        attribution="Državna volilna komisija Republike Slovenije",
    )
    for url in RAW_FILES:
        p.artifact(raw.cached(SOURCE, url), DVK_SOURCE)
    p.add("body", id=CHAMBER_BODY, polity_id=SLOVENIA, kind="lower_house")
    p.name(CHAMBER_BODY, "National Assembly", "en", "official")
    p.name(CHAMBER_BODY, "Državni zbor", "sl", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=SLOVENIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Slovenia", "en", "official")
    p.name(PRESIDENCY, "Predsednik Republike Slovenije", "sl", "native", False)
    for code, (since, rules, english, native) in SYSTEM_RULES.items():
        id = SYSTEMS[code]
        p.add(
            "electoral_system", id=id, polity_id=SLOVENIA, valid_from=since, valid_to=None, rules=rules, rules_version=1
        )
        p.name(id, english, "en", "official")
        p.name(id, native, "sl", "native", False)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=METHOD[code], lang="en")
    return p


def _overfull(p: Package, sheet: Sheet) -> None:
    found: Counter[str] = Counter()
    for row in sheet.rows:
        if row.registered and row.cast and row.cast > row.registered:
            found[station_kind(row.place)] += 1
    if not found:
        return
    parts = ", ".join(
        f"{n} {kind.replace('_', ' ')} station{'s' if n > 1 else ''}" for kind, n in sorted(found.items())
    )
    p.accept(
        "accounting.turnout",
        f"the commission prints more ballots in the box than registered voters at {parts}: early-voting, "
        "any-station (OMNIA), mission, and postal stations have no roll of their own, so the few voters registered "
        "there are those holding voting certificates, and a standard station can exceed its roll by the same "
        "certificates; the figures are stored as printed",
    )


def _facts(
    p: Package,
    sheet: Sheet,
    result_set: str | dict[int, str],
    units: list[str],
    options: Mapping[tuple[int, int], str],
) -> None:
    """Stage the accounting and option values of every station row."""
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []

    def add(rs: str, unit: str, option: str | None, measure: int, value: int) -> None:
        sets.append(rs)
        unit_ids.append(unit)
        option_ids.append(option)
        measures.append(measure)
        values.append(value)

    for row, unit in zip(sheet.rows, units, strict=True):
        electoral_unit, _ = district(row.ve, row.vo)
        rs = result_set if isinstance(result_set, str) else result_set[electoral_unit or 0]
        # Stations without a roll of their own print a roll of zero beside real ballots; the zero is not a count.
        if row.registered is not None and (row.registered > 0 or not row.cast):
            add(rs, unit, None, 2, row.registered)
        for measure, value in ((14, row.voters), (3, row.cast), (5, row.invalid), (4, row.valid)):
            if value is not None:
                add(rs, unit, None, measure, value)
        for index, count in enumerate(row.options):
            option = options.get((electoral_unit or 0, index) if isinstance(result_set, dict) else (0, index))
            if option is None:
                if count:
                    raise ValueError(f"option {index} has votes in unit {electoral_unit} but no ballot line")
                continue
            if count is not None:
                add(rs, unit, option, 7 if option in PREFERENCE else 1, count)
    size = len(values)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": sets,
                "unit_id": unit_ids,
                "option_id": option_ids,
                "channel": [0] * size,
                "measure": measures,
                "count_round": [0] * size,
                "flags": [0] * size,
                "value": values,
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        ),
    )
    _overfull(p, sheet)


def _units(p: Package, vote: Vote, sheet: Sheet) -> list[str]:
    counts = Counter((row.ve, row.vo, row.stv) for row in sheet.rows)
    units: list[str] = []
    for row in sheet.rows:
        code = f"{vote.day}:{row.ve}:{row.vo}:{row.stv}"
        if counts[(row.ve, row.vo, row.stv)] > 1:
            code += f":{changeset.slug(row.place)}"
        unit = key("unit", code)
        units.append(unit)
        electoral_unit, seat = district(row.ve, row.vo)
        if electoral_unit is None:
            relation, place = "unlocated", None
        else:
            relation = "within"
            place = (
                area("si.vo", f"{electoral_unit}.{seat}") if seat is not None else area("si.ve", str(electoral_unit))
            )
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=SLOVENIA,
            scheme_code="si.volisce",
            code=code,
            area_relation=relation,
            area_id=place,
            kind=station_kind(row.place),
        )
        if row.place:
            p.name(unit, row.place, "sl", "official")
    if len(set(units)) != len(units):
        raise ValueError(f"{vote.day}: polling-station codes collide")
    return units


def _result_set(p: Package, election_id: str, contest: str, vote: Vote, tag: str) -> str:
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=DVK_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD[vote.kind],
        lang="en",
    )
    return result_set


def _declare(p: Package, result_set: str, sheet: Sheet, options: Mapping[int, str]) -> None:
    """Store the printed national total as the declared figures of a nationwide contest."""
    total = sheet.total
    if total is None:
        return
    for measure, value in (
        (2, total.registered),
        (14, total.voters),
        (3, total.cast),
        (5, total.invalid),
        (4, total.valid),
    ):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for index, option in options.items():
        value = total.options[index]
        if value is not None:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option,
                measure=7 if option in PREFERENCE else 1,
                value=value,
            )


def _election(p: Package, vote: Vote, kind: str, slug_path: str) -> str:
    id = key("election", vote.kind, vote.day)
    p.add("election", id=id, polity_id=SLOVENIA, kind=kind, date_from=vote.day, date_to=vote.day, status="held")
    p.name(id, vote.title, "en", "official")
    p.name(id, vote.native, "sl", "native", False)
    p.add("slug", path=slug_path, entity_id=id, is_current=True)
    return id


def election(vote: Vote, sheet: Sheet) -> Package:
    build = {"dz": assembly, "pres": president, "ep": european, "ref": referendum}[vote.kind]
    return build(vote, sheet)


def _package(vote: Vote, summary: str, depends: list[str] | None = None) -> Package:
    p = Package(slug(vote), SOURCE, summary, depends=["si.dvk/geo", "si.dvk/registry", *(depends or [])])
    p.external.add(SLOVENIA)
    p.uses(raw.cached(SOURCE, vote.url))
    return p


def _ballot_line(name: str, index: int) -> tuple[str, int]:
    label = clean(name)
    found = re.match(r"(\d+)\.\s*(.*)", label)
    return (found.group(2), int(found.group(1))) if found else (label, index + 1)


def assembly(vote: Vote, sheet: Sheet) -> Package:
    p = _package(vote, f"The {vote.day.year} Slovenian National Assembly election by polling station")
    election_id = _election(p, vote, "general", f"si/{vote.day}-general")
    full: dict[int, str] = {}
    if vote.day.year == 2011:
        book = fastexcel.read_excel(str(raw.cached(SOURCE, vote.url).path))
        for line in book.load_sheet_by_name("Šifrant list", header_row=0, dtypes="string").to_polars().rows():
            full[int(line[0])] = clean(line[1])
    lines = [_ballot_line(name, index) for index, name in enumerate(sheet.options)]
    parties: dict[int, str] = {}
    for index, (label, position) in enumerate(lines):
        party = parties[index] = key("party", vote.day, position, label)
        official = full.get(position, label)
        p.add("party", id=party, kind="party", polity_id=SLOVENIA)
        p.name(party, official, "sl", "official")
        if official != label:
            p.name(party, label, "sl", "short", False)
    units = _units(p, vote, sheet)
    by_unit: dict[int, list[Row]] = {}
    for row in sheet.rows:
        electoral_unit, _ = district(row.ve, row.vo)
        if electoral_unit is None:
            raise ValueError(f"{vote.day}: station {row.place} is outside the 8 electoral units")
        by_unit.setdefault(electoral_unit, []).append(row)
    result_sets: dict[int, str] = {}
    options: dict[tuple[int, int], str] = {}
    for unit in ELECTORAL_UNITS:
        name = title(next(row.ve_name for row in by_unit[unit] if row.ve_name))
        contest = key("contest", vote.day, unit)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=CHAMBER_BODY,
            district_area_id=area("si.ve", str(unit)),
            magnitude=11,
            round_number=1,
            electoral_system_id=SYSTEMS["dz"],
            default_result_kind="counted",
        )
        p.name(contest, f"Electoral unit {unit} ({name})", "en", "official")
        p.name(contest, f"{unit}. volilna enota ({name})", "sl", "native", False)
        p.add(
            "slug", path=f"si/{vote.day}-general/unit-{unit}-{changeset.slug(name)}", entity_id=contest, is_current=True
        )
        result_sets[unit] = _result_set(p, election_id, contest, vote, f"{vote.day}:{unit}")
        for index, (label, position) in enumerate(lines):
            if not any(row.options[index] for row in by_unit[unit]):
                continue
            option = options[(unit, index)] = key("option", vote.day, unit, position, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=parties[index],
                ballot_position=position,
                ballot_label=full.get(position, label),
            )
    _facts(p, sheet, result_sets, units, options)
    return p


def president(vote: Vote, sheet: Sheet) -> Package:
    first = next(v for v in VOTES if v.kind == "pres" and v.round == 1 and v.day.year == vote.day.year)
    p = _package(
        vote,
        f"The {vote.day.year} Slovenian presidential election, {'first' if vote.round == 1 else 'second'} round, "
        "by polling station",
        depends=[] if vote.round == 1 else [slug(first)],
    )
    election_id = _election(p, vote, "general" if vote.round == 1 else "runoff", f"si/{vote.day}-presidential")
    contest = key("contest", vote.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=vote.round,
        previous_round_id=None if vote.round == 1 else key("contest", first.day),
        electoral_system_id=SYSTEMS["pres"],
        default_result_kind="counted",
    )
    p.name(contest, vote.title, "en", "official")
    p.add("slug", path=f"si/{vote.day}-presidential/president", entity_id=contest, is_current=True)
    result_set = _result_set(p, election_id, contest, vote, str(vote.day))
    options: dict[int, str] = {}
    for index, name in enumerate(sheet.options):
        name = clean(name)
        person = key("person", f"pres:{vote.day.year}:{name}")
        option = options[index] = key("option", vote.day, index + 1, name)
        if vote.round == 1:
            p.add("person", id=person)
            p.name(person, title(name), "sl", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=index + 1,
            ballot_label=title(name),
        )
        p.add(
            "candidacy",
            id=key("candidacy", vote.day, index + 1, name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=title(name),
        )
    units = _units(p, vote, sheet)
    _facts(p, sheet, result_set, units, {(0, index): option for index, option in options.items()})
    _declare(p, result_set, sheet, options)
    return p


def european(vote: Vote, sheet: Sheet) -> Package:
    p = _package(vote, "The 2014 European Parliament election in Slovenia by polling station")
    election_id = _election(p, vote, "general", f"si/{vote.day}-european-parliament")
    contest = key("contest", vote.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        magnitude=8,
        round_number=1,
        electoral_system_id=SYSTEMS["ep"],
        default_result_kind="counted",
    )
    p.name(contest, vote.title, "en", "official")
    p.add("slug", path=f"si/{vote.day}-european-parliament/slovenia", entity_id=contest, is_current=True)
    result_set = _result_set(p, election_id, contest, vote, str(vote.day))
    options: dict[int, str] = {}
    current: str | None = None
    position = 0
    seen: set[str] = set()
    for index, name in enumerate(sheet.options):
        label = clean(name)
        if re.match(r"\d+\.", label):
            text, number = _ballot_line(label, index)
            party = key("party", vote.day, number, text)
            p.add("party", id=party, kind="party", polity_id=SLOVENIA)
            p.name(party, text, "sl", "official")
            current = options[index] = key("option", vote.day, number, text)
            p.add(
                "contest_option",
                id=current,
                contest_id=contest,
                kind="list",
                party_id=party,
                ballot_position=number,
                ballot_label=text,
            )
            position = 0
            continue
        if current is None or label in seen:
            raise ValueError(f"{label} is not a candidate of a list")
        seen.add(label)
        position += 1
        person = key("person", f"ep:{vote.day}:{label}")
        option = options[index] = key("option", vote.day, current, position, label)
        PREFERENCE.add(option)
        p.add("person", id=person)
        p.name(person, title(label), "sl", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=current,
            ballot_position=position,
            ballot_label=title(label),
        )
        p.add(
            "candidacy",
            id=key("candidacy", vote.day, current, position, label),
            person_id=person,
            option_id=option,
            role="list_member",
            list_position=position,
            ballot_name=title(label),
        )
    units = _units(p, vote, sheet)
    _facts(p, sheet, result_set, units, {(0, index): option for index, option in options.items()})
    _declare(p, result_set, sheet, options)
    return p


def referendum(vote: Vote, sheet: Sheet) -> Package:
    p = _package(vote, f"The {vote.day} Slovenian referendum, {vote.note}, by polling station")
    election_id = _election(p, vote, "referendum", f"si/{vote.day}-referendum")
    contest = key("contest", vote.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="ballot_question",
        round_number=1,
        electoral_system_id=SYSTEMS["ref"],
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, vote.note, "en", "official")
    p.add("slug", path=f"si/{vote.day}-referendum/law", entity_id=contest, is_current=True)
    result_set = _result_set(p, election_id, contest, vote, str(vote.day))
    if [plain(name) for name in sheet.options] != ["za", "proti"]:
        raise ValueError(f"{vote.day}: expected ZA and PROTI columns, found {sheet.options}")
    options: dict[int, str] = {}
    for index, (label, position) in enumerate((("For", 1), ("Against", 2))):
        option = options[index] = key("option", vote.day, label)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )
    units = _units(p, vote, sheet)
    _facts(p, sheet, result_set, units, {(0, index): option for index, option in options.items()})
    _declare(p, result_set, sheet, options)
    return p
