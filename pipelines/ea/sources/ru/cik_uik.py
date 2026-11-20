"""Russia's State Duma elections of 2003 to 2026, federal list vote by precinct election commission (UIK).

The Central Election Commission publishes each election's protocols by precinct commission on its results site
(izbirkom.ru), which refuses scripted requests. The tables here are the Commission's precinct protocols as Sergey
Shpilkin (2003 to 2021) and Petr Zhizhin (2026) read them from that site, published as one CSV file per election in
the `data/` folder of Dmitry Kobak's `dkobak/elections` repository. Every file is fetched from one pinned commit of
that repository, so the raw cache records exactly which copy was read; the Commission remains the source of record.

The files hold the federal list vote only: the single-member district votes of 2003, 2016, 2021, and 2026 are not
in them. Ballots cast are the ballots found in the portable and stationary boxes, and voters are the ballots
issued (early, in the polling station, and outside it); the other lines of the protocol (ballots received,
cancelled, lost, and absentee certificates) are read only to check the files.

Areas: the 1993 federal subjects come from `ru.cik`'s geo package. The five subjects that mergers formed between
2005 and 2008 (Perm, Krasnoyarsk, and Kamchatka krais, the enlarged Irkutsk Oblast, and Zabaykalsky Krai) are
added here with their geoBoundaries outlines. Polling stations abroad and in Baikonur are unlocated abroad units.
Polling stations in Crimea and Sevastopol, which Russia counts in its national totals, are kept in the result set
as unlocated units: the archive does not place Ukrainian territory under Russia.
"""

import csv
import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ru import cik

SOURCE = "ru.cik_uik"
RUSSIA = cik.RUSSIA
TERRITORY = cik.TERRITORY
DUMA = cik.DUMA
SUBJECT = cik.SUBJECT
SHAPES = cik.SHAPES
LIST_2003 = cik.LIST_SYSTEM
UIK = "ru.uik"
DERIVED = 1

REPOSITORY = "https://github.com/dkobak/elections"
COMMIT = "a6530610baf200b15442239511a1375c51ba1cb5"
FILES = f"https://raw.githubusercontent.com/dkobak/elections/{COMMIT}/data/"
README = f"{FILES}README.md"
LAYER = cik.LAYER
META = cik.META


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def results_url(year: int) -> str:
    return f"{FILES}{year}.csv.zip"


@dataclass(frozen=True)
class Election:
    year: int
    since: date
    day: date
    seats: int
    list_seats: int
    # The seats each list won, as the Commission's protocol of the federal district's results declares them; the
    # keys are the party keys of `party_key`.
    won: dict[str, int]


ELECTIONS = [
    Election(
        2003,
        date(2003, 12, 7),
        date(2003, 12, 7),
        450,
        225,
        {"единаяроссия": 120, "кпрф": 40, "лдпр": 36, "родинанароднопатриотическийсоюз": 29},
    ),
    Election(
        2007,
        date(2007, 12, 2),
        date(2007, 12, 2),
        450,
        450,
        {"единаяроссия": 315, "кпрф": 57, "лдпр": 40, "справедливаяроссия": 38},
    ),
    Election(
        2011,
        date(2011, 12, 4),
        date(2011, 12, 4),
        450,
        450,
        {"единаяроссия": 238, "кпрф": 92, "справедливаяроссия": 64, "лдпр": 56},
    ),
    Election(
        2016,
        date(2016, 9, 18),
        date(2016, 9, 18),
        450,
        225,
        {"единаяроссия": 140, "кпрф": 35, "лдпр": 34, "справедливаяроссия": 16},
    ),
    Election(
        2021,
        date(2021, 9, 17),
        date(2021, 9, 19),
        450,
        225,
        {"единаяроссия": 126, "кпрф": 48, "лдпр": 19, "справедливаяроссия": 19, "новыелюди": 13},
    ),
    Election(2026, date(2026, 9, 20), date(2026, 9, 20), 450, 225, {}),
]
BY_YEAR = {e.year: e for e in ELECTIONS}

# The registered voters the repository's README gives for each file and for the Commission's own national protocol,
# where the two differ; the README says the other files' sums agree with the Commission's.
README_REGISTERED = {
    2003: (108_913_062, 108_906_249),
    2007: (109_148_196, 109_145_517),
    2011: (109_229_337, None),
    2016: (110_061_200, None),
    2021: (109_204_662, None),
    2026: (100_963_451, None),
}

# ----------------------------------------------------------------------------------------------------------------
# Federal subjects


@dataclass(frozen=True)
class Merged:
    iso: str
    name: str
    since: date


# The subjects formed by merging a 1993 subject with the autonomous okrugs inside it; each is a new area from the
# day the merger took effect, and the 2017 geoBoundaries layer draws it.
MERGED = [
    Merged("PER", "Пермский край", date(2005, 12, 1)),
    Merged("KYA", "Красноярский край", date(2007, 1, 1)),
    Merged("KAM", "Камчатский край", date(2007, 7, 1)),
    Merged("IRK", "Иркутская область", date(2008, 1, 1)),
    Merged("ZAB", "Забайкальский край", date(2008, 3, 1)),
]
BY_MERGED = {m.iso: m for m in MERGED}
ABROAD = cik.ABROAD
CRIMEA = "krym"
SEVASTOPOL = "sevastopol"
ANNEXED = {CRIMEA, SEVASTOPOL}


def merged_area(iso: str) -> str:
    return key("area", SUBJECT, iso)


def subject_code(region: str) -> str:
    """The ISO 3166-2 suffix of the subject a row's region names, or a marker for abroad and annexed territory."""
    folded = re.sub(r"[^а-я]", "", region.lower().replace("ё", "е"))
    if folded in ("зарубежнаятерритория", "иностранныеучастки"):
        return ABROAD
    if "крым" in folded:
        return CRIMEA
    if "севастопол" in folded:
        return SEVASTOPOL
    if "забайкальск" in folded:
        return "ZAB"
    return cik.subject(region)


def subject_area(iso: str, day: date) -> str:
    merged = BY_MERGED.get(iso)
    if merged is not None and day >= merged.since:
        return merged_area(iso)
    if iso == "ZAB":
        raise LookupError(f"Zabaykalsky Krai did not exist on {day}")
    return cik.subject_area(iso)


# ----------------------------------------------------------------------------------------------------------------
# Raw files


def fetch() -> None:
    raw.fetch(SOURCE, README, suffix=".md")
    for e in ELECTIONS:
        raw.fetch(SOURCE, results_url(e.year), suffix=".zip")
    raw.fetch(SOURCE, LAYER, suffix=".geojson")
    raw.fetch(SOURCE, META, suffix=".json")


def rows(year: int) -> tuple[list[str], list[list[str]]]:
    with zipfile.ZipFile(raw.cached(SOURCE, results_url(year)).path) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"{year}: expected one CSV in the archive, found {names}")
        text = archive.read(names[0]).decode("utf-8")
    table = list(csv.reader(io.StringIO(text)))
    return table[0], table[1:]


# ----------------------------------------------------------------------------------------------------------------
# Columns and parties


@dataclass
class Columns:
    region: int
    tik: int
    uik: int
    oik: int | None
    registered: int
    issued: list[int]
    boxes: list[int]
    cast: int | None
    invalid: int
    valid: int
    lists: list[tuple[int | None, str, int]]
    against: int | None


LEADING = re.compile(r"^(\d+)\.\s*")


def columns(year: int, header: list[str]) -> Columns:
    def find(*parts: str, without: str = "") -> int:
        found = [i for i, h in enumerate(header) if all(p in h for p in parts) and not (without and without in h)]
        if len(found) != 1:
            raise LookupError(f"{year}: {len(found)} columns match {parts}")
        return found[0]

    if year == 2026:
        start = header.index("suspicious") + 1
        return Columns(
            region=header.index("region"),
            tik=header.index("tik"),
            uik=header.index("uik_num"),
            oik=None,
            registered=header.index("registered"),
            issued=[],
            boxes=[],
            cast=header.index("turnout"),
            invalid=header.index("invalid"),
            valid=header.index("valid"),
            lists=[(None, h, i) for i, h in enumerate(header) if i >= start],
            against=None,
        )
    lists: list[tuple[int | None, str, int]] = []
    for i, h in enumerate(header):
        if (m := LEADING.match(h)) is not None:
            lists.append((int(m.group(1)), h[m.end() :], i))
    return Columns(
        region=header.index("region"),
        tik=header.index("tik"),
        uik=header.index("uik"),
        oik=header.index("oik") if "oik" in header else None,
        registered=find("Число избирателей, внесенных"),
        issued=[
            find("проголосовавшим досрочно"),
            find("выданных", "помещени", without="вне"),
            find("выданных", "вне помещени"),
        ]
        if year != 2003
        else [
            find("проголосовавшим досрочно"),
            find("выданных избирателям на избирательном участке"),
            find("вне помещения"),
        ],
        boxes=[find("переносных ящиках"), find("стационарных ящиках")],
        cast=None,
        invalid=find("Число недействительных"),
        valid=find("Число действительных"),
        lists=lists,
        against=header.index("Против всех") if "Против всех" in header else None,
    )


PREFIXES = (
    "общественная организация ",
    "всероссийская политическая партия ",
    "социалистическая политическая партия ",
    "кпрф - политическая партия ",
    "политическая партия ",
)
# Names under which one party stood in different elections, by the folded form of each name.
ALIASES = {
    "коммунистическаяпартияроссийскойфедерации": "кпрф",
    "коммунистическаяпартияроссийскойфедерациикпрф": "кпрф",
    "либеральнодемократическаяпартияроссии": "лдпр",
    "лдпрлиберальнодемократическаяпартияроссии": "лдпр",
    "справедливаяроссияродинапенсионерыжизнь": "справедливаяроссия",
    "российскаядемократическаяпартияяблоко": "яблоко",
    "российскаяобъединеннаядемократическаяпартияяблоко": "яблоко",
    "политическаяпартияединаяроссия": "единаяроссия",
}
BLOCS = {
    "родинанароднопатриотическийсоюз",
    "российскаяпартияпенсионеровипартиясоциальнойсправедливости",
    "новыйкурсавтомобильнаяроссия",
    "великаяроссияевразийскийсоюз",
    "партиявозрожденияроссиироссийскаяпартияжизни",
}


def party_key(printed: str) -> str:
    text = printed.lower().replace("ё", "е")
    changed = True
    while changed:
        changed = False
        stripped = text.strip(' "«»')
        for prefix in PREFIXES:
            if stripped.startswith(prefix):
                text, changed = stripped[len(prefix) :], True
    folded = re.sub(r"[^а-яa-z]", "", text)
    if folded.startswith(("справедливаяроссия", "партиясправедливаяроссия")):
        return "справедливаяроссия"
    return ALIASES.get(folded, folded)


def display(printed: str) -> str:
    """A list's name as printed, without the quotation marks the 2003 file wraps around it."""
    text = " ".join(printed.split())
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
        if text.count('"') % 2:
            text = text.replace('" (', " (") if '" (' in text else f'{text}"'
    return text


# ----------------------------------------------------------------------------------------------------------------
# Packages


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/duma-{e.year}" for e in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The five federal subjects that mergers formed from 2005 to 2008, and the scheme of precinct election "
        "commissions (UIK) the State Duma results of 2003 to 2026 are reported by",
    )
    p.external |= {RUSSIA, TERRITORY, SHAPES}
    p.artifact(raw.cached(SOURCE, LAYER), SHAPES)
    p.artifact(raw.cached(SOURCE, META), SHAPES)
    p.add(
        "scheme",
        code=UIK,
        polity_id=RUSSIA,
        kind="precinct",
        rank=4,
        noun="precinct election commission",
        code_system=None,
    )
    features = json.loads(raw.cached(SOURCE, LAYER).path.read_text(encoding="utf-8"))["features"]
    shapes = {
        str(f["properties"]["shapeISO"]).removeprefix("RU-"): shapely.make_valid(shapely.geometry.shape(f["geometry"]))
        for f in features
    }
    p.external.add(SUBJECT_SCHEME)
    for m in MERGED:
        id = merged_area(m.iso)
        p.add("area", id=id, scheme_code=SUBJECT)
        p.add("area_parent", area_id=id, parent_id=TERRITORY, valid_from=m.since, valid_to=None)
        p.name(id, m.name, "ru", "official")
        _outline(p, id, shapes[m.iso], m.since)
    return p


SUBJECT_SCHEME = SUBJECT


def _outline(p: Package, id: str, shape: shapely.Geometry, since: date) -> None:
    gridded = shapely.set_precision(shapely.make_valid(shape), cik.GRID)
    polygons = [part for part in shapely.get_parts(gridded) if shapely.get_type_id(part) == 3 and not part.is_empty]
    outline = shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=since,
        valid_to=None,
        geom=shapely.to_wkb(outline, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
        ),
        quality="osm",
        source_id=SHAPES,
    )


def source_id() -> str:
    return key("source", "cec-uik")


LIST_2007 = key("electoral_system", "duma", "list", 2007)
LIST_2016 = key("electoral_system", "duma", "list", 2016)

SYSTEMS = [
    (
        LIST_2007,
        date(2005, 5, 18),
        date(2014, 2, 22),
        {
            "formula": "hare",
            "threshold": {"level": "list", "scope": "national", "percent": 7},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "All 450 deputies of the State Duma are elected from federal lists of parties in one nationwide district, "
            "by largest remainders with the Hare quota among the lists that win at least 7% of the votes (Law No. "
            "51-ФЗ of 18 May 2005). From 2011, a list with 5 to 6% of the votes was to receive one seat and a list with "
            "6 to 7% two seats; no list fell in that range in 2011."
        ),
    ),
    (
        LIST_2016,
        date(2014, 2, 22),
        None,
        {
            "formula": "hare",
            "threshold": {"level": "list", "scope": "national", "percent": 5},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "Half of the State Duma's 450 deputies, 225, are elected from federal lists of parties in one nationwide "
            "district, by largest remainders with the Hare quota among the lists that win at least 5% of the votes "
            "(Law No. 20-ФЗ of 22 February 2014, art. 3); the other 225 are elected in single-member districts by "
            "plurality."
        ),
    ),
]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The State Duma's federal list systems of 2007 and 2016 and the parties and blocs on the federal lists of "
        "2003 to 2026, from the Central Election Commission's precinct protocols",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(RUSSIA)
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher="Центральная избирательная комиссия Российской Федерации",
        title="Выборы депутатов Государственной Думы: результаты по участковым избирательным комиссиям, 2003-2026",
        url="http://www.vybory.izbirkom.ru/region/izbirkom",
        license="Official election results of a state body; not subject to copyright (Civil Code of the Russian "
        "Federation, art. 1259(6))",
        license_url="https://www.consultant.ru/document/cons_doc_LAW_64629/",
        attribution="Центральная избирательная комиссия Российской Федерации; tables compiled by Sergey Shpilkin, "
        "Ivan Shukshin, and Petr Zhizhin",
        notes=f"The Commission's precinct protocols as read from its results site by Sergey Shpilkin (2003-2021) and "
        f"Petr Zhizhin (2026), published in the data folder of {REPOSITORY} and fetched from commit {COMMIT}. The "
        "Commission's site refuses scripted requests.",
    )
    p.artifact(raw.cached(SOURCE, README), source_id())
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=RUSSIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=p.id("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    seen: dict[str, str] = {}
    for e in ELECTIONS:
        header, _ = rows(e.year)
        for _, printed, _ in columns(e.year, header).lists:
            k = party_key(printed)
            if k in seen:
                continue
            seen[k] = printed
            id = party(k)
            p.add("party", id=id, kind="bloc" if k in BLOCS else "party", polity_id=RUSSIA)
            p.name(id, display(printed), "ru", "official")
    for e in ELECTIONS:
        missing = set(e.won) - set(seen)
        if missing:
            raise LookupError(f"{e.year}: no list for the seat holders {sorted(missing)}")
    return p


def party(k: str) -> str:
    return key("party", k)


def integer(text: str) -> int:
    value = text.strip()
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"not a count: {text!r}")
    return int(value)


def uik_code(uik: str, oik: str | None) -> str:
    found = re.search(r"(\d+)", uik)
    if found is not None:
        return found.group(1)
    if oik is not None and (district := re.search(r"(\d+)", oik)) is not None:
        return f"deg-{district.group(1)}"
    raise LookupError(f"no number for the precinct {uik!r} ({oik!r})")


LIST_METHOD = (
    "The federal list vote by precinct election commission (UIK), from the Commission's precinct protocols as "
    "compiled in the dkobak/elections repository. Registered voters, invalid and valid ballots, and each list's "
    "votes are the protocol's lines. Voters are the ballots issued early, in the polling station, and outside it; "
    "ballots cast are the ballots found in the portable and stationary boxes; both are sums of protocol lines."
)
LIST_METHOD_2026 = (
    "The federal list vote by precinct election commission (UIK), from the Commission's precinct protocols as "
    "Petr Zhizhin compiled them for the dkobak/elections repository. That table gives registered voters, the "
    "ballots found in the boxes (its turnout column), invalid and valid ballots, and each list's votes; it gives no "
    "ballots issued and no ballot positions."
)


def election(e: Election) -> Package:
    year, day = e.year, e.day
    p = Package(
        f"{SOURCE}/duma-{year}",
        SOURCE,
        f"The {year} State Duma election: the federal list vote by precinct election commission, from the Central "
        "Election Commission's precinct protocols",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {RUSSIA, TERRITORY, DUMA, SUBJECT}
    p.artifact(raw.cached(SOURCE, results_url(year)), source_id())
    p.uses(raw.cached(SOURCE, README))
    header, body = rows(year)
    cols = columns(year, header)
    if year == 2003:
        p.accept(
            "accounting.votes_valid",
            "two 2003 precinct protocols print list votes (and votes against all) that add up to 1 and 3 more "
            "than their valid ballots (398 against 397, 745 against 742); stored as printed",
        )
        p.accept(
            "accounting.turnout",
            "three 2003 precinct protocols print more ballots in the boxes than registered voters (2,810 against "
            "2,802, 2,085 against 2,036, and 1,013 against 1,000); stored as printed",
        )

    path = f"ru/{day}-general"
    election_id = p.id("election", year)
    p.add(
        "election",
        id=election_id,
        polity_id=RUSSIA,
        kind="general",
        date_from=e.since,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{year} Russian State Duma election", "en", "official")
    p.name(
        election_id,
        f"Выборы депутатов Государственной Думы Федерального Собрания Российской Федерации {year} года",
        "ru",
        "native",
        False,
    )
    if year == 2003:
        system = LIST_2003
        p.external.add(LIST_2003)
    else:
        system = LIST_2007 if year < 2016 else LIST_2016
    contest = p.id("contest", year, "list")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=DUMA,
        district_area_id=TERRITORY,
        magnitude=e.list_seats,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest, "Федеральный избирательный округ", "ru", "official")
    p.add("slug", path=f"{path}/federal-list", entity_id=contest, is_current=True)

    result_set = p.id("result_set", year, "list")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source_id(),
        status="active",
        is_default=True,
        completeness=None if year == 2026 else 1.0,
    )
    p.add(
        "note",
        id=p.id("note", result_set, "method"),
        entity_id=result_set,
        kind="methodology",
        body_md=LIST_METHOD_2026 if year == 2026 else LIST_METHOD,
        lang="en",
    )
    for kind, text in caveats(year):
        p.add("note", id=p.id("note", result_set, kind), entity_id=result_set, kind="caveat", body_md=text, lang="en")

    options: dict[int, str] = {}
    keys: dict[int, str] = {}
    for ballot, printed, column in cols.lists:
        k = party_key(printed)
        option = p.id("option", year, "list", k)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(k),
            ballot_position=ballot,
            ballot_label=display(printed),
        )
        options[column] = option
        keys[column] = k
    if cols.against is not None:
        options[cols.against] = p.id("option", year, "list", "against")
        p.add(
            "contest_option",
            id=options[cols.against],
            contest_id=contest,
            kind="none_of_the_above",
            ballot_label="Против всех",
        )
    won_seen: set[str] = set()
    for column, option in options.items():
        seats = e.won.get(keys.get(column, ""))
        if seats:
            won_seen.add(keys[column])
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
    if won_seen != set(e.won):
        raise LookupError(f"{year}: seat holders without a list: {sorted(set(e.won) - won_seen)}")

    units: dict[str, int] = {}
    unit_rows: list[dict[str, object]] = []
    unit_names: list[dict[str, object]] = []
    values: dict[str, list[object]] = {"unit_id": [], "option_id": [], "measure": [], "flags": [], "value": []}

    def value(unit: str, option: str | None, measure: int, number: int, flags: int = 0) -> None:
        values["unit_id"].append(unit)
        values["option_id"].append(option)
        values["measure"].append(measure)
        values["flags"].append(flags)
        values["value"].append(number)

    totals: Counter[str] = Counter()
    for row in body:
        region = row[cols.region].strip()
        iso = subject_code(region)
        tik = row[cols.tik].strip()
        if "Байконур" in tik:
            iso = ABROAD
        number = uik_code(row[cols.uik], row[cols.oik] if cols.oik is not None else None)
        code = f"{day}:{iso}:{number}"
        if code in units:
            raise ValueError(f"{year}: the precinct {code} appears twice")
        unit = p.id("unit", UIK, code)
        units[code] = 1
        if iso == ABROAD:
            relation, area, kind = "unlocated", None, "abroad_mission"
        elif iso in ANNEXED:
            relation, area, kind = "unlocated", None, "standard"
        else:
            relation, area, kind = "within", subject_area(iso, day), "standard"
            if area != merged_area(iso):
                p.external.add(area)
        unit_rows.append(
            {
                "id": unit,
                "polity_id": RUSSIA,
                "scheme_code": UIK,
                "code": code,
                "area_relation": relation,
                "area_id": area,
                "kind": kind,
            }
        )
        label = (
            f"УИК № {number}"
            if not number.startswith("deg-")
            else f"Дистанционное электронное голосование, {row[cols.oik or 0]}"
        )
        unit_names.append({"entity": unit, "text": f"{label} ({region}, {tik})" if tik else f"{label} ({region})"})
        registered = integer(row[cols.registered])
        invalid = integer(row[cols.invalid])
        valid = integer(row[cols.valid])
        value(unit, None, 2, registered)
        if cols.cast is not None:
            cast = integer(row[cols.cast])
            value(unit, None, 3, cast)
        else:
            voters = sum(integer(row[i]) for i in cols.issued)
            cast = sum(integer(row[i]) for i in cols.boxes)
            value(unit, None, 14, voters, DERIVED)
            value(unit, None, 3, cast, DERIVED)
            totals["voters"] += voters
        value(unit, None, 5, invalid)
        value(unit, None, 4, valid)
        totals["registered"] += registered
        totals["cast"] += cast
        totals["invalid"] += invalid
        totals["valid"] += valid
        votes = 0
        for column, option in options.items():
            n = integer(row[column])
            votes += n
            value(unit, option, 1, n)
            totals[option] += n
        if votes != valid:
            totals["votes_mismatch"] += 1
    n = len(values["value"])
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": [result_set] * n,
                "unit_id": values["unit_id"],
                "option_id": values["option_id"],
                "channel": [0] * n,
                "measure": values["measure"],
                "count_round": [0] * n,
                "flags": values["flags"],
                "value": values["value"],
            },
            schema_overrides={"value": pl.Int64, "measure": pl.Int16, "channel": pl.Int16, "flags": pl.Int32},
        ),
    )
    p.frame("reporting_unit", pl.DataFrame(unit_rows))
    p.frame(
        "name",
        pl.DataFrame(
            {
                "id": [p.id("name", u["entity"], "ru", "official", u["text"]) for u in unit_names],
                "entity_id": [u["entity"] for u in unit_names],
                "text": [u["text"] for u in unit_names],
                "lang": ["ru"] * len(unit_names),
                "kind": ["official"] * len(unit_names),
                "is_preferred": [True] * len(unit_names),
            }
        ),
    )
    stated, cec = README_REGISTERED[year]
    if totals["registered"] != stated:
        raise ValueError(f"{year}: registered voters sum to {totals['registered']:,}, the README states {stated:,}")
    report = {
        "year": year,
        "precincts": len(units),
        "registered": totals["registered"],
        "cec_registered": cec if cec is not None else stated,
        "voters": totals["voters"],
        "cast": totals["cast"],
        "invalid": totals["invalid"],
        "valid": totals["valid"],
        "votes_not_valid": totals["votes_mismatch"],
        "lists": {display(printed): totals[options[column]] for _, printed, column in cols.lists},
    }
    if cols.against is not None:
        report["lists"]["Против всех"] = totals[options[cols.against]]
    out = package.folder(p.key)
    out.mkdir(parents=True, exist_ok=True)
    (out / "totals.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    return p


def caveats(year: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if year in (2016, 2021, 2026):
        out.append(
            (
                "annexed",
                (
                    "The Commission's totals include polling stations in Crimea and Sevastopol. They are kept here so "
                    "the national figures match the Commission's, as reporting units with no area: the archive does not "
                    "place Ukrainian territory under Russia."
                ),
            )
        )
    if year == 2003:
        out.append(
            (
                "registered",
                (
                    "The precinct protocols sum to 108,913,062 registered voters against the 108,906,249 of the "
                    "Commission's national protocol; the difference sits in Bashkortostan, Moscow Oblast, and "
                    "Novosibirsk Oblast, likely where results were later cancelled at some polling stations. 24 polling "
                    "stations report no ballots and are stored as printed."
                ),
            )
        )
    if year == 2007:
        out.append(
            (
                "registered",
                (
                    "The precinct protocols sum to 109,148,196 registered voters against the 109,145,517 of the "
                    "Commission's national protocol; 11 polling stations that report no ballots account for the "
                    "difference and are stored as printed."
                ),
            )
        )
    if year == 2021:
        out.append(
            (
                "remote",
                (
                    "Remote electronic voting is reported as its own precinct: in Moscow as precincts 5001 to 5015, and "
                    "in the other regions that used it as one precinct per single-member district, coded deg-<district>."
                ),
            )
        )
    if year == 2026:
        out.append(
            (
                "incomplete",
                (
                    "The compiled table holds 89,132 polling stations. It leaves out remote electronic voting, which the "
                    "Commission counts in its totals, and the polling stations in the four Ukrainian regions Russia "
                    "claimed in 2022, so the national figures fall short of the Commission's. No declared seats are "
                    "stored for this election."
                ),
            )
        )
    return out
