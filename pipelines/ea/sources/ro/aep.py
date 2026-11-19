import re
from datetime import date
from pathlib import Path
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "ro.aep"
ROMANIA = str(uuid5(package.NAMESPACE, "polity\x1fRO"))
PORTAL = "https://data.gov.ro/organization/autoritatea-electorala-permanenta"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
POSTAL = 3
EP_LISTS = 13
ALL = 0
HOST = "https://data.gov.ro/dataset"

URLS = {
    "2020-cd": f"{HOST}/8c0e5b4d-6d8d-4068-9194-8d57e3d63333/resource/b9ac87a7-3387-470d-848a-4a1a36920221/download/rezultate-finale-vot-sectie-cd.csv",
    "2020-s": f"{HOST}/8c0e5b4d-6d8d-4068-9194-8d57e3d63333/resource/b7ae8806-cf9b-4b0a-8733-59e184d42e97/download/rezultate-finale-vot-sectie-senat.csv",
    "2020-cd-post": f"{HOST}/8c0e5b4d-6d8d-4068-9194-8d57e3d63333/resource/6d0a66cc-480b-42e4-b4d9-4a721998a4e5/download/rezultate-finale-vot-corespondenta-cd.csv",
    "2020-s-post": f"{HOST}/8c0e5b4d-6d8d-4068-9194-8d57e3d63333/resource/23a08003-bfb1-4b51-86e0-5f21b523a0d5/download/rezultate-finale-vot-corespondenta-senat.csv",
    "2020-cd-seats": f"{HOST}/8c0e5b4d-6d8d-4068-9194-8d57e3d63333/resource/7e5f0a95-8390-4ce6-a09c-55b4347840d2/download/mandate-repartizate-camera-deputailor.csv",
    "2020-s-seats": f"{HOST}/8c0e5b4d-6d8d-4068-9194-8d57e3d63333/resource/44f26f33-3ea7-46d9-9e17-7caac27cf545/download/mandate-repartizate-senat.csv",
    "2016-cd": f"{HOST}/eb0770eb-d78d-4227-9186-74a08f19d068/resource/e863c174-89ea-49be-8865-b32b3cdbc71e/download/siap2016cd2016121419150015-2.csv",
    "2016-s": f"{HOST}/eb0770eb-d78d-4227-9186-74a08f19d068/resource/da9aeace-ed6e-4527-b8b8-52e8d86e92e6/download/siap2016s2016121419150015-3.csv",
    "2016-cd-post": f"{HOST}/eb0770eb-d78d-4227-9186-74a08f19d068/resource/a397ed83-39bf-4369-85cf-8c6b92b4d642/download/bvccd-1.csv",
    "2016-s-post": f"{HOST}/eb0770eb-d78d-4227-9186-74a08f19d068/resource/978d9ace-7773-4f27-bbfc-4e97b56e4bbe/download/bvcs-1.csv",
    "2016-cd-seats": f"{HOST}/eb0770eb-d78d-4227-9186-74a08f19d068/resource/cf4cb1ce-6b00-4f11-8bde-e54d506d1e1e/download/mandate-pe-competitori-pe-circumscripii-cd.xlsx",
    "2016-s-seats": f"{HOST}/eb0770eb-d78d-4227-9186-74a08f19d068/resource/8e2b05cd-e996-4d01-bd82-a705ed4b779c/download/mandate-pe-competitori-pe-circumscripii-s.xlsx",
    "2020-local": f"{HOST}/a9ac2859-b244-479b-a3ab-dd077999babb/resource/2fa331fd-5890-48b4-9c2f-7aa8e1c12835/download/bd_sectii_27.09.202024.01.2021.csv",
    "2020-runoff": f"{HOST}/a9ac2859-b244-479b-a3ab-dd077999babb/resource/8be694cf-b14b-44f5-ad6a-7d87da1ad945/download/bd_p_11.10.2020.csv",
    "2020-cj": f"{HOST}/a9ac2859-b244-479b-a3ab-dd077999babb/resource/cdfa62a7-ee8d-4a3f-bcee-cbfc560d26a2/download/pv_final_cntry_cj.csv",
    "2020-cl": f"{HOST}/a9ac2859-b244-479b-a3ab-dd077999babb/resource/334e34f1-29a0-47c3-96cc-1cfd09b6b72a/download/pv_final_cntry_cl.csv",
    "2020-p": f"{HOST}/a9ac2859-b244-479b-a3ab-dd077999babb/resource/dd5512c1-7f6f-4f90-9008-6b68553973b6/download/pv_final_cntry_p.csv",
    "2020-pcj": f"{HOST}/a9ac2859-b244-479b-a3ab-dd077999babb/resource/bab685cf-1939-4203-86a2-db1f7a65cc32/download/pv_final_cntry_pcj.csv",
    "2020-p2": f"{HOST}/a9ac2859-b244-479b-a3ab-dd077999babb/resource/128c0dde-a961-44ea-9332-72f67ac94ad0/download/pv_final_cntry_p_tur2.csv",
    "2020-legend": f"{HOST}/a9ac2859-b244-479b-a3ab-dd077999babb/resource/1dfdfda8-4f50-46ef-8f3d-8e7bab096911/download/legenda.txt",
    "2019-ro": f"{HOST}/0d2548eb-a211-4321-a503-5520cedeeb97/resource/ce4cc92d-a239-4492-a7d8-e956bfb6ad25/download/rezultateromania.csv",
    "2019-abroad": f"{HOST}/0d2548eb-a211-4321-a503-5520cedeeb97/resource/ea7dc2a0-2c93-4fb0-ad6a-383f94d84a6a/download/rezultatestrainatate.csv",
    "2019-legend": f"{HOST}/0d2548eb-a211-4321-a503-5520cedeeb97/resource/9b897f4d-2998-476b-94e1-5675922736a7/download/legenda-eurparlamentare-2019.xlsx",
}
SUFFIX = {"xlsx": ".xlsx"}
DAYS = {
    "2016-12-11": date(2016, 12, 11),
    "2019-05-26": date(2019, 5, 26),
    "2020-09-27": date(2020, 9, 27),
    "2020-10-11": date(2020, 10, 11),
    "2020-12-06": date(2020, 12, 6),
}
MINORITY = re.compile(r"^(UNIUNEA|ASOCIAȚIA|FEDERAȚIA|COMUNITATEA|FORUMUL) ")
NOT_MINORITY = {
    "UNIUNEA DEMOCRATĂ MAGHIARĂ DIN ROMÂNIA",
    "UNIUNEA SALVAȚI ROMÂNIA",
    "UNIUNEA NAȚIONALĂ PENTRU PROGRESUL ROMÂNIEI",
}
INDEPENDENT = re.compile(r"^(?:CANDIDAT INDEPENDENT\s*-\s*(?P<a>.+)|(?P<b>.+?)\s*-\s*CANDIDAT INDEPENDENT)$")
FOLD = str.maketrans({"Ş": "Ș", "ş": "ș", "Ţ": "Ț", "ţ": "ț", "“": "", "”": "", '"': "", "–": "-", "„": ""})
METHOD = (
    "Counted per polling station and published by the Permanent Electoral Authority as open data. Registered voters "
    "are the voters on the permanent lists and the copies of the special and supplementary lists. Ballots cast are "
    "the voters who took part. Valid votes are the votes for lists and candidates, invalid ballots are the null "
    "votes, and, where the file prints a further column after them, it is stored as blank ballots."
)
POST_METHOD = (
    "Votes cast by post from abroad are counted by the Authority in units of their own, apart from the polling "
    "stations. For them the ballots cast are the ballots counted, the valid votes are the valid ballots among them, "
    "and the invalid ballots are the null ones."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


AEP_SOURCE = key("source", "aep")
LOCAL_COUNCIL = key("body", "local-council")
COUNTY_COUNCIL = key("body", "county-council")
BUCHAREST_COUNCIL = key("body", "bucharest-council")
MAYOR = key("office", "mayor")
GENERAL_MAYOR = key("office", "general-mayor")
COUNTY_PRESIDENT = key("office", "county-council-president")
SYSTEM_PLURALITY = key("electoral_system", "plurality")
CHAMBER = key("body", "chamber")
SENATE = key("body", "senate")
SYSTEM_LIST = key("electoral_system", "list", "dhondt")


def canon(text: object) -> str:
    value = " ".join(str(text or "").translate(FOLD).split()).upper()
    return re.sub(r"\s*-\s*", "-", value)


def number(value: object) -> int | None:
    text = str(value or "").strip().replace(" ", "")
    if text in ("", "None", "-"):
        return None
    return int(float(text))


def independent(label: str) -> str | None:
    found = INDEPENDENT.match(canon(label))
    return (found.group("a") or found.group("b")) if found else None


def is_minority(label: str) -> bool:
    text = canon(label)
    return bool(MINORITY.match(text)) and text not in NOT_MINORITY


def fetch() -> None:
    for url in URLS.values():
        suffix = Path(url).suffix
        raw.fetch(SOURCE, url, suffix=suffix)
    from ea.sources.ro import aep_local2016

    aep_local2016.fetch()
    from ea.sources.ro import aep_2012

    aep_2012.fetch()
    from ea.sources.ro import aep_referendum

    aep_referendum.fetch()


def packages() -> list[str]:
    from ea.sources.ro import aep_2012, aep_2019, aep_local2016, aep_referendum

    return [
        "ro.aep/registry",
        *(f"ro.aep/{day}" for day in DAYS),
        *aep_local2016.PACKAGES,
        *aep_2012.PACKAGES,
        *aep_2019.PACKAGES,
        *aep_referendum.packages(),
    ]


def parse() -> None:
    from ea.sources.ro import aep_2012, aep_local2016

    registry().write()
    for day in DAYS:
        build(day).write()
    aep_local2016.parse()
    aep_2012.parse()
    from ea.sources.ro import aep_2019

    aep_2019.parse()
    from ea.sources.ro import aep_referendum

    aep_referendum.parse()


def emit() -> None:
    package.run("load", packages())


def csv(name: str, separator: str = ";", encoding: str = "utf-8") -> pl.DataFrame:
    data = raw.cached(SOURCE, URLS[name]).path.read_bytes()
    text = data.decode(encoding).lstrip("﻿")
    return pl.read_csv(text.encode(), separator=separator, infer_schema_length=0, truncate_ragged_lines=True)


def sheet(name: str) -> list[list[str | None]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, URLS[name]).path))
    frame = book.load_sheet_by_name(book.sheet_names[0], header_row=None, dtypes="string").to_polars()
    return [[None if cell is None else str(cell) for cell in row] for row in frame.rows()]


MAIN_COLUMNS = ["a", "a1", "a2", "a3", "b", "b1", "b2", "b3", "c", "d", "e", "f", "g"]
POST_COLUMNS = ["a", "b", "b1", "b2", "c", "c1", "c2", "d", "d1", "d2"]
ACCOUNTING = {"registered": "a", "cast": "b", "valid": "e", "invalid": "f", "blank": "g"}
POST_ACCOUNTING = {"registered": "a", "cast": "d", "valid": "d1", "invalid": "d2"}


def standard(frame: pl.DataFrame, meta: list[str], postal: bool) -> pl.DataFrame:
    """Name the printed columns; what follows the accounting columns are the ballot lines, kept as printed."""
    accounting = POST_COLUMNS if postal else MAIN_COLUMNS
    columns = frame.columns
    count = len(meta) + len(accounting)
    options = [c for c in columns[count:] if not c.startswith("Data trimiterii")]
    renamed = {c: canon(c) for c in options}
    mapping = POST_ACCOUNTING if postal else ACCOUNTING
    named = frame.select(
        *(pl.col(old).alias(new) for old, new in zip(columns[: len(meta)], meta, strict=True)),
        *(
            pl.col(old).cast(pl.Int64, strict=False).alias(f"_{new}")
            for old, new in zip(columns[len(meta) : count], accounting, strict=True)
        ),
        *(pl.col(option).cast(pl.Int64, strict=False).alias(renamed[option]) for option in options),
    )
    options = list(renamed.values())
    named = named.with_columns(
        pl.col("circ").cast(pl.Int64),
        pl.col("nr").str.strip_chars(),
        pl.lit(postal).alias("postal"),
        *(pl.col(f"_{old}").alias(new) for new, old in mapping.items()),
    )
    if postal:
        named = named.with_columns(
            pl.when(pl.col("registered") > 0).then(pl.col("registered")).otherwise(None).alias("registered"),
            pl.lit(None, pl.Int64).alias("blank"),
        )
    keep = ["circ", "cname", "nr", "place", "postal", "registered", "cast", "valid", "invalid", "blank"]
    if "siruta" not in named.columns:
        named = named.with_columns(pl.lit(None, pl.String).alias("siruta"))
    return named.select(*keep, "siruta", *options)


def chamber_table(day: str, which: str, postal: bool) -> pl.DataFrame:
    name = f"{day[:4]}-{which}" + ("-post" if postal else "")
    if day == "2020-12-06":
        frame = csv(name)
        return standard(frame, ["circ", "cname", "place", "nr", "uat", "siruta"], postal)
    if postal:
        frame = csv(name, encoding="cp1250")
        return standard(frame, ["tip", "circ", "cname", "nr", "place"], True)
    frame = csv(name, ",", "utf-16")
    return standard(frame, ["row", "country", "circ", "cname", "nr", "place", "siruta"], False)


def label_of(column: str) -> str:
    return " ".join(column.split())


def options_of(frame: pl.DataFrame) -> list[str]:
    return frame.columns[frame.columns.index("siruta") + 1 :]


def read_registry_labels() -> dict[str, str]:
    found: dict[str, str] = {}
    for day in ("2016-12-11", "2020-12-06"):
        for which in ("cd", "s"):
            for postal in (False, True):
                for column in options_of(chamber_table(day, which, postal)):
                    label = label_of(column)
                    if independent(label) is None:
                        found.setdefault(canon(label), label)
    for name in ("2020-local", "2020-runoff"):
        for competitor in local_table(name)["Competitor"].unique().to_list():
            if independent(competitor) is None:
                found.setdefault(canon(competitor), label_of(competitor))
    for number, column in legend().items():
        if int(number[1:]) <= EP_LISTS:
            found.setdefault(canon(column), column)
    return found


def legend() -> dict[str, str]:
    """The 2019 European Parliament option names, by printed column."""
    names: dict[str, str] = {}
    for row in sheet("2019-legend"):
        found = re.match(r"\s*(g\d+)\s*=\s*(.+?)\s*$", row[0] or "")
        if found:
            names[found.group(1)] = " ".join(found.group(2).split())
    return names


def party_id(label: str) -> str:
    return key("party", canon(label))


def registry() -> Package:
    p = Package(
        "ro.aep/registry",
        SOURCE,
        "Romanian parties and minority organizations, the Chamber of Deputies and the Senate, and their electoral "
        "system, from the Permanent Electoral Authority's open data",
    )
    p.external.add(ROMANIA)
    p.add(
        "source",
        id=AEP_SOURCE,
        kind="official_body",
        publisher="Autoritatea Electorală Permanentă",
        title="Election results published as open data by the Permanent Electoral Authority",
        url=PORTAL,
        license="Open Government Licence 1.0 (data.gov.ro)",
        attribution="Autoritatea Electorală Permanentă",
    )
    for url in URLS.values():
        p.artifact(raw.cached(SOURCE, url), AEP_SOURCE)
    p.add(
        "scheme", code="ro.circumscription", polity_id=ROMANIA, kind="electoral_district", rank=1, noun="constituency"
    )
    p.add("scheme", code="ro.sv", polity_id=ROMANIA, kind="precinct", rank=2, noun="polling station")
    for body, kind, english, romanian in (
        (CHAMBER, "lower_house", "Chamber of Deputies of Romania", "Camera Deputaților"),
        (SENATE, "upper_house", "Senate of Romania", "Senatul României"),
    ):
        p.add("body", id=body, polity_id=ROMANIA, kind=kind)
        p.name(body, english, "en", "official")
        p.name(body, romanian, "ro", "native", False)
    for body, kind, english, romanian in (
        (LOCAL_COUNCIL, "council", "Local councils of Romania", "Consiliile locale"),
        (COUNTY_COUNCIL, "council", "County councils of Romania", "Consiliile județene"),
        (BUCHAREST_COUNCIL, "council", "General Council of Bucharest", "Consiliul General al Municipiului București"),
    ):
        p.add("body", id=body, polity_id=ROMANIA, kind=kind)
        p.name(body, english, "en", "official")
        p.name(body, romanian, "ro", "native", False)
    for office, kind, english, romanian in (
        (MAYOR, "mayor", "Mayor of a Romanian municipality, town, or commune", "Primar"),
        (GENERAL_MAYOR, "mayor", "General Mayor of Bucharest", "Primarul General al Municipiului București"),
        (COUNTY_PRESIDENT, "other", "President of a county council", "Președintele consiliului județean"),
    ):
        p.add("office", id=office, polity_id=ROMANIA, kind=kind)
        p.name(office, english, "en", "official")
        p.name(office, romanian, "ro", "native", False)
    p.add("electoral_system", id=SYSTEM_PLURALITY, polity_id=ROMANIA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM_PLURALITY),
        entity_id=SYSTEM_PLURALITY,
        kind="methodology",
        body_md="The candidate with the most votes in a single round is elected.",
        lang="en",
    )
    p.add("electoral_system", id=SYSTEM_LIST, polity_id=ROMANIA, rules={"formula": "dhondt"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM_LIST),
        entity_id=SYSTEM_LIST,
        kind="methodology",
        body_md="Seats are allocated to party lists in each constituency. Thresholds and the rules for minority "
        "organizations are not stored here.",
        lang="en",
    )
    for id, label in sorted(((party_id(canon(c)), c) for c in read_registry_labels()), key=lambda item: item[1]):
        p.add("party", id=id, kind="bloc" if canon(label).startswith("ALIAN") else "party", polity_id=ROMANIA)
        p.name(id, label, "ro", "official")
    return p


def accept(p: Package) -> None:
    p.accept(
        "accounting.ballots_sum",
        "some polling units print valid, null, and further ballots that do not add up to the voters who took part; "
        "stored as printed",
    )
    p.accept(
        "accounting.turnout",
        "some polling units print more voters than registered voters on the lists, mostly abroad and on special "
        "lists; stored as printed",
    )


def build(day: str) -> Package:
    first = DAYS[day]
    p = Package(
        f"ro.aep/{day}",
        SOURCE,
        f"The Romanian elections of {first} by polling station, from the Permanent Electoral Authority's open data",
        depends=["ro.aep/registry", *(["ro.aep/2020-09-27"] if day == "2020-10-11" else [])],
    )
    p.external.add(ROMANIA)
    if day == "2019-05-26":
        european(p, day, first)
    elif day == "2020-09-27":
        local(p, day, first)
    elif day == "2020-10-11":
        runoff(p, day, first)
    else:
        parliament(p, day, first)
    accept(p)
    return p


def unit_rows(p: Package, frame: pl.DataFrame, day: str, add: bool = True) -> pl.DataFrame:
    """Add each polling unit once, and return the frame with its unit id."""
    frame = frame.with_columns(
        pl.when(pl.col("postal"))
        .then(pl.format("{}:p{}/{}/{}", pl.lit(day), pl.col("circ"), pl.col("nr"), pl.col("place").str.to_uppercase()))
        .otherwise(pl.format("{}:{}/{}", pl.lit(day), pl.col("circ"), pl.col("nr")))
        .alias("code")
    )
    units = frame.select("code", "postal", "circ", "cname", "nr", "place").unique(
        "code", keep="first", maintain_order=True
    )
    if units.height != frame.height:
        raise ValueError(f"{day}: {frame.height - units.height} duplicate unit codes")
    ids = [key("unit", code) for code in units["code"]]
    if not add:
        return frame.with_columns(pl.Series("unit", ids))
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": ids,
                "polity_id": ROMANIA,
                "scheme_code": "ro.sv",
                "code": units["code"],
                "area_relation": "unlocated",
                "kind": ["postal_district" if post else "standard" for post in units["postal"]],
            }
        ),
    )
    names = []
    for post, circ, cname, nr, place in units.select("postal", "circ", "cname", "nr", "place").iter_rows():
        where = " ".join(str(place or "").split())
        what = "postal votes" if post else "station"
        names.append(f"{where} ({cname.title() if cname else circ}, {what} {nr})" if where else f"{cname}, {what} {nr}")
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


def values(p: Package, result_set: str, frame: pl.DataFrame, options: dict[str, str], accounting: bool = True) -> None:
    """Stage the accounting and option values of each unit in `frame`; `options` maps column to option id."""
    measures = (("registered", REGISTERED), ("cast", CAST), ("valid", VALID), ("invalid", INVALID), ("blank", BLANK))
    parts = []
    for column, measure in measures if accounting else ():
        part = frame.filter(pl.col(column).is_not_null()).select(
            "unit",
            "postal",
            pl.col(column).alias("value"),
            pl.lit(measure, pl.Int16).alias("measure"),
            pl.lit(None, pl.String).alias("option_id"),
        )
        parts.append(part)
    for column, option in options.items():
        part = frame.filter(pl.col(column).is_not_null()).select(
            "unit",
            "postal",
            pl.col(column).alias("value"),
            pl.lit(VOTES, pl.Int16).alias("measure"),
            pl.lit(option).alias("option_id"),
        )
        parts.append(part)
    stacked = pl.concat(parts)
    p.frame(
        "result_value",
        stacked.select(
            pl.lit(result_set).alias("result_set_id"),
            pl.col("unit").alias("unit_id"),
            "option_id",
            pl.when(pl.col("postal")).then(POSTAL).otherwise(ALL).cast(pl.Int16).alias("channel"),
            "measure",
            pl.lit(0, pl.Int16).alias("count_round"),
            pl.lit(0, pl.Int16).alias("flags"),
            "value",
        ),
    )


def make_election(p: Package, name: str, day: date, english: str, romanian: str, path: str) -> str:
    election = key("election", name)
    p.add("election", id=election, polity_id=ROMANIA, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, romanian, "ro", "native", False)
    return election


def make_contest(
    p: Package, name: str, election: str, english: str, romanian: str, path: str, **fields: object
) -> tuple[str, str]:
    contest = key("contest", name)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        round_number=1,
        default_result_kind="counted",
        **fields,
    )
    p.name(contest, english, "en", "official")
    p.name(contest, romanian, "ro", "native", False)
    p.add("slug", path=path, entity_id=contest, is_current=True)
    result_set = key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=AEP_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    return contest, result_set


def seats_2020(which: str) -> dict[tuple[int, str], int]:
    frame = csv(f"2020-{which}-seats")
    columns = frame.columns
    return {
        (int(circ), canon(label)): int(n)
        for circ, label, n in frame.select(columns[0], columns[2], columns[3]).iter_rows()
    }


def seats_2016(which: str) -> dict[tuple[int, str], int]:
    rows = sheet(f"2016-{which}-seats")
    header = [canon(cell) for cell in rows[0]]
    found: dict[tuple[int, str], int] = {}
    for row in rows[1:]:
        if row[0] and row[0].isdigit():
            for label, cell in zip(header[2:], row[2:], strict=True):
                if cell and int(cell):
                    found[(int(row[0]), label)] = int(cell)
    return found


def parliament(p: Package, day: str, first: date) -> None:
    year = first.year
    for url_name in [f"{year}-{w}{s}" for w in ("cd", "s") for s in ("", "-post", "-seats")]:
        p.uses(raw.cached(SOURCE, URLS[url_name]))
    election = make_election(
        p,
        f"ps{day}",
        first,
        f"{year} Romanian parliamentary election",
        f"Alegeri parlamentare din {first.day}.{first.month:02d}.{year}",
        f"ro/{day}-general",
    )
    unmatched: list[str] = []
    for which, body, english, romanian, slug in (
        ("cd", CHAMBER, "Chamber of Deputies", "Camera Deputaților", "chamber"),
        ("s", SENATE, "Senate", "Senat", "senate"),
    ):
        main = chamber_table(day, which, False)
        post = chamber_table(day, which, True)
        seats = seats_2020(which) if year == 2020 else seats_2016(which)
        labels = options_of(main)
        frame = unit_rows(p, pl.concat([main, post], how="diagonal"), day, which == "cd")
        # a code is unique across the chambers, so the second chamber's units are the same rows
        circs = sorted(frame["circ"].unique().to_list())
        minorities = [
            c
            for c in labels
            if is_minority(label_of(c))
            and frame.filter(pl.col(c).is_not_null()).select(pl.col("circ").n_unique()).item() > 1
        ]
        groups = [(circ, labels) for circ in circs]
        if minorities and which == "cd":
            groups.append((99, minorities))
        for circ, columns in groups:
            part = frame if circ == 99 else frame.filter(pl.col("circ") == circ)
            columns = [c for c in columns if part[c].null_count() < part.height]
            name = part["cname"][0]
            if circ == 99:
                magnitude = sum(n for (c, _), n in seats.items() if c == 99) or 17
                title = ("national minorities", "minorități naționale")
            else:
                magnitude = sum(n for (c, _), n in seats.items() if c == circ) or None
                title = (name.title(), name.title())
            contest, result_set = make_contest(
                p,
                f"{day}-{which}-{circ}",
                election,
                f"{year} Romanian {english} election, {title[0]}",
                f"Alegeri pentru {romanian}, {title[1]}",
                f"ro/{day}-general/{slug}/{circ}",
                body_id=body,
                magnitude=magnitude,
                electoral_system_id=None if circ == 99 else SYSTEM_LIST,
            )
            options: dict[str, str] = {}
            for position, column in enumerate(columns, 1):
                label = label_of(column)
                option = key("option", f"{day}-{which}-{circ}", canon(label))
                options[column] = option
                person = independent(label)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if person else "list",
                    party_id=None if person else party_id(label),
                    ballot_label=person or label,
                )
                if person:
                    who = key("person", f"{day}-{which}-{circ}", canon(person))
                    p.add("person", id=who)
                    p.name(who, person.title(), "ro", "official")
                    p.add(
                        "candidacy",
                        id=key("candidacy", f"{day}-{which}-{circ}", canon(person)),
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=person.title(),
                        is_independent=True,
                    )
            values(p, result_set, part, options, circ != 99)
            for column, option in options.items():
                n = seats.get((circ, canon(label_of(column))))
                if n:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=n)
            matched = {canon(label_of(c)) for c in options}
            unmatched += [label for (c, label), n in seats.items() if c == circ and label not in matched and n]
    if unmatched:
        raise ValueError(f"seats without a ballot line: {unmatched[:10]}")


def european(p: Package, day: str, first: date) -> None:
    for name in ("2019-ro", "2019-abroad", "2019-legend"):
        p.uses(raw.cached(SOURCE, URLS[name]))
    names = legend()
    frames = []
    for name in ("2019-ro", "2019-abroad"):
        data = csv(name, ",")
        frames.append(
            data.select(
                pl.col("Cod birou electoral").cast(pl.Int64).alias("circ"),
                pl.col("Județ").alias("cname"),
                pl.col("Nr").str.strip_chars().alias("nr"),
                pl.concat_str(["Uat", "Localitate", "Secție"], separator=" — ", ignore_nulls=True).alias("place"),
                pl.lit(False).alias("postal"),
                pl.col("a").cast(pl.Int64).alias("registered"),
                pl.col("b").cast(pl.Int64).alias("cast"),
                pl.col("e").cast(pl.Int64).alias("valid"),
                pl.col("f").cast(pl.Int64).alias("invalid"),
                pl.lit(None, pl.Int64).alias("blank"),
                *(pl.col(f"g{i}").cast(pl.Int64) for i in range(1, len(names) + 1)),
            )
        )
    frame = unit_rows(p, pl.concat(frames), day)
    election = make_election(
        p,
        "ep2019",
        first,
        "2019 European Parliament election in Romania",
        "Alegeri pentru Parlamentul European din 26.05.2019",
        f"ro/{day}-european",
    )
    contest, result_set = make_contest(
        p,
        "ep2019",
        election,
        "2019 European Parliament election in Romania",
        "Alegeri pentru Parlamentul European, România",
        f"ro/{day}-european/romania",
        magnitude=32,
    )
    options: dict[str, str] = {}
    for position, (column, label) in enumerate(names.items(), 1):
        option = key("option", "ep2019", position)
        options[column] = option
        listed = int(column[1:]) <= EP_LISTS
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list" if listed else "candidate",
            party_id=party_id(label) if listed else None,
            ballot_position=position,
            ballot_label=label,
        )
        if not listed:
            who = key("person", "ep2019", label)
            p.add("person", id=who)
            p.name(who, label.title(), "ro", "official")
            p.add(
                "candidacy",
                id=key("candidacy", "ep2019", label),
                person_id=who,
                option_id=option,
                role="head",
                ballot_name=label.title(),
                is_independent=True,
            )
    values(p, result_set, frame, options)


def local_table(name: str) -> pl.DataFrame:
    return csv(name).with_columns(pl.col("Competitor").str.strip_chars())


LOCAL = {
    # Tip: (council or office, scope, body or office id, system, English noun, Romanian noun, slug)
    "CJ": ("list", "county", COUNTY_COUNCIL, SYSTEM_LIST, "County council", "Consiliul județean", "county-council"),
    "PCJ": (
        "office",
        "county",
        COUNTY_PRESIDENT,
        SYSTEM_PLURALITY,
        "County council president",
        "Președinte de consiliu județean",
        "county-president",
    ),
    "CL": ("list", "uat", LOCAL_COUNCIL, SYSTEM_LIST, "Local council", "Consiliul local", "local-council"),
    "P": ("office", "uat", MAYOR, SYSTEM_PLURALITY, "Mayor", "Primar", "mayor"),
    "CGMB": (
        "list",
        "all",
        BUCHAREST_COUNCIL,
        SYSTEM_LIST,
        "General Council of Bucharest",
        "Consiliul General al Municipiului București",
        "bucharest-council",
    ),
    "PG": (
        "office",
        "all",
        GENERAL_MAYOR,
        SYSTEM_PLURALITY,
        "General Mayor of Bucharest",
        "Primarul General al Municipiului București",
        "general-mayor",
    ),
}
LOCAL_METHOD = (
    "Counted per polling station and published by the Permanent Electoral Authority as open data. Registered voters "
    "are the voters on the lists, ballots cast are the voters who took part, valid votes are the valid votes, and "
    "invalid ballots are the null votes. A mayoral candidate and a county council president appear under the party "
    "or alliance that nominated them, or as an independent; the station file prints no candidate names for parties. "
    "The file covers the elections of 27 September 2020 and the partial elections of 24 January 2021 without dating "
    "its rows."
)
ACCOUNTING_LOCAL = {"registered": "a", "cast": "b", "valid": "c", "invalid": "d"}


def local_units(p: Package, data: pl.DataFrame, day: str) -> pl.DataFrame:
    """Add one unit per polling station and return the rows with the unit id."""
    stations = data.select("county_nce", "county_name", "nr_sv", "den_sv", "uat_name").unique(
        ["county_nce", "nr_sv"], keep="first", maintain_order=True
    )
    codes = [f"{day}:{c}/{n}" for c, n in zip(stations["county_nce"], stations["nr_sv"], strict=True)]
    ids = [key("unit", code) for code in codes]
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": ids,
                "polity_id": ROMANIA,
                "scheme_code": "ro.sv",
                "code": codes,
                "area_relation": "unlocated",
                "kind": "standard",
            }
        ),
    )
    names = [
        f"{' '.join(str(den or '').split())} ({' '.join(str(uat).split()).title()}, station {nr})"
        for den, uat, nr in zip(stations["den_sv"], stations["uat_name"], stations["nr_sv"], strict=True)
    ]
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
    lookup = pl.DataFrame({"county_nce": stations["county_nce"], "nr_sv": stations["nr_sv"], "unit": ids})
    return data.join(lookup, on=["county_nce", "nr_sv"], how="left")


def stage_values(p: Package, result_sets: pl.DataFrame, rows: pl.DataFrame, options: pl.DataFrame) -> None:
    """`rows` holds one line per unit, contest key, and competitor; `options` maps contest and competitor to ids."""
    accounting = rows.unique(["unit", "contest"], keep="first", maintain_order=True)
    parts = []
    for column, measure in (("registered", REGISTERED), ("cast", CAST), ("valid", VALID), ("invalid", INVALID)):
        parts.append(
            accounting.select(
                "contest",
                "unit",
                pl.lit(None, pl.String).alias("option_id"),
                pl.lit(measure, pl.Int16).alias("measure"),
                pl.col(ACCOUNTING_LOCAL[column]).cast(pl.Int64, strict=False).alias("value"),
            ).filter(pl.col("value").is_not_null())
        )
    joined = rows.join(options, on=["contest", "competitor"], how="left")
    parts.append(
        joined.select(
            "contest",
            "unit",
            pl.col("option_id"),
            pl.lit(VOTES, pl.Int16).alias("measure"),
            pl.col("VVE").cast(pl.Int64, strict=False).alias("value"),
        ).filter(pl.col("value").is_not_null())
    )
    stacked = pl.concat(parts).join(result_sets, on="contest", how="left")
    p.frame(
        "result_value",
        stacked.select(
            "result_set_id",
            pl.col("unit").alias("unit_id"),
            "option_id",
            pl.lit(0, pl.Int16).alias("channel"),
            "measure",
            pl.lit(0, pl.Int16).alias("count_round"),
            pl.lit(0, pl.Int16).alias("flags"),
            "value",
        ),
    )


def runoff(p: Package, day: str, first: date) -> None:
    local(p, day, first, True)


def local(p: Package, day: str, first: date, second: bool = False) -> None:
    uses = ("2020-runoff",) if second else ("2020-local", "2020-cj", "2020-cl", "2020-p", "2020-pcj", "2020-legend")
    for used in uses:
        p.uses(raw.cached(SOURCE, URLS[used]))
    election_name = "local2020-runoff" if second else "local2020"
    table = local_table("2020-runoff") if second else local_table("2020-local")
    if second:
        table = table.rename({"TVVE": "VVE"}).with_columns(pl.lit("P").alias("Tip"))
    data = local_units(p, table, day)
    election = key("election", election_name)
    p.add(
        "election",
        id=election,
        polity_id=ROMANIA,
        kind="runoff" if second else "local",
        date_from=first,
        date_to=first,
        status="held",
    )
    suffix = "-runoff" if second else ""
    p.add("slug", path=f"ro/{day}-local{suffix}", entity_id=election, is_current=True)
    p.name(
        election,
        "2020 Romanian local elections, second round" if second else "2020 Romanian local elections",
        "en",
        "official",
    )
    p.name(
        election,
        "Alegeri locale, turul II, 11.10.2020" if second else "Alegeri locale din 27.09.2020",
        "ro",
        "native",
        False,
    )
    built: dict[str, dict[str, set[str]]] = {}
    for tip, (kind, scope, holder, system, english, romanian, slug) in LOCAL.items():
        if second and tip != "P":
            continue
        part = data.filter(pl.col("Tip") == tip)
        scopes = {
            "county": pl.col("county_nce"),
            "uat": pl.col("uat_siruta"),
            "all": pl.lit("all"),
        }[scope]
        part = part.with_columns(
            pl.format("{}-{}-{}", pl.lit("local2020r" if second else "local2020"), pl.lit(tip), scopes).alias(
                "contest"
            ),
            pl.col("Competitor").map_elements(canon, return_dtype=pl.String).alias("competitor"),
        )
        contests = part.select(
            "contest",
            pl.col("county_name").first().over("contest").alias("county_name"),
            pl.col("uat_name").first().over("contest").alias("uat_name"),
            pl.col("county_nce").first().over("contest").alias("county_nce"),
            pl.col("uat_siruta").first().over("contest").alias("uat_siruta"),
        ).unique("contest", keep="first", maintain_order=True)
        contest_ids, set_ids, rows_contest, names_en, names_ro, slugs, previous = [], [], [], [], [], [], []
        for contest, county, uat, county_nce, siruta in contests.select(
            "contest", "county_name", "uat_name", "county_nce", "uat_siruta"
        ).iter_rows():
            where = {
                "county": f"{county.title()} County" if county_nce != "42" else "Bucharest",
                "uat": " ".join(str(uat).split()).title(),
                "all": "",
            }[scope]
            where_ro = where.replace(" County", "").replace("Bucharest", "București") if scope == "county" else where
            cid = key("contest", contest)
            rid = key("result_set", contest)
            contest_ids.append(cid)
            set_ids.append(rid)
            rows_contest.append(contest)
            names_en.append(
                f"2020 Romanian local elections, {english}"
                + (f", {where}" if where else "")
                + (", second round" if second else "")
            )
            names_ro.append(
                f"{romanian}" + (f", {where_ro}" if where_ro else "") + (", turul II" if second else "") + ", 2020"
            )
            slug_key = {"county": county_nce, "uat": siruta, "all": "bucharest"}[scope]
            previous.append(key("contest", f"local2020-P-{siruta}") if second else None)
            slugs.append(f"ro/{day}-local{suffix}/{slug}/{slug_key}")
        fields: dict[str, object] = {
            "election_id": election,
            "kind": "list_allocation" if kind == "list" else "office",
            "round_number": 2 if second else 1,
            "default_result_kind": "counted",
            "electoral_system_id": system,
        }
        if kind == "list":
            fields["body_id"] = holder
        else:
            fields["office_id"] = holder
            fields["magnitude"] = 1
        p.frame(
            "contest",
            pl.DataFrame({"id": contest_ids, "previous_round_id": previous, **fields}),
        )
        p.frame(
            "name",
            pl.DataFrame(
                {
                    "id": [p.id("name", c, lang, "official") for c in contest_ids for lang in ("en", "ro")],
                    "entity_id": [c for c in contest_ids for _ in range(2)],
                    "text": [t for pair in zip(names_en, names_ro, strict=True) for t in pair],
                    "lang": ["en", "ro"] * len(contest_ids),
                    "kind": ["official", "native"] * len(contest_ids),
                    "is_preferred": [True, False] * len(contest_ids),
                }
            ),
        )
        p.frame("slug", pl.DataFrame({"path": slugs, "entity_id": contest_ids, "is_current": True}))
        p.frame(
            "result_set",
            pl.DataFrame(
                {
                    "id": set_ids,
                    "election_id": election,
                    "contest_id": contest_ids,
                    "stage": "official",
                    "kind": "counted",
                    "source_id": AEP_SOURCE,
                    "status": "active",
                    "is_default": True,
                    "completeness": 1.0,
                }
            ),
        )
        p.frame(
            "note",
            pl.DataFrame(
                {
                    "id": [key("note", r) for r in set_ids],
                    "entity_id": set_ids,
                    "kind": "methodology",
                    "body_md": LOCAL_METHOD,
                    "lang": "en",
                }
            ),
        )
        pairs = part.select("contest", "competitor", "Competitor").unique(
            ["contest", "competitor"], keep="first", maintain_order=True
        )
        option_rows = []
        for contest, competitor, label in pairs.iter_rows():
            label = label_of(label)
            option = key("option", contest, competitor)
            person = independent(label)
            option_rows.append((contest, competitor, option, label, person))
        options = pl.DataFrame(
            {
                "contest": [r[0] for r in option_rows],
                "competitor": [r[1] for r in option_rows],
                "option_id": [r[2] for r in option_rows],
            }
        )
        p.frame(
            "contest_option",
            pl.DataFrame(
                {
                    "id": [r[2] for r in option_rows],
                    "contest_id": [key("contest", r[0]) for r in option_rows],
                    "kind": ["candidate" if kind == "office" or r[4] else "list" for r in option_rows],
                    "party_id": [None if r[4] else party_id(r[3]) for r in option_rows],
                    "ballot_label": [r[4] or r[3] for r in option_rows],
                }
            ),
        )
        people = [r for r in option_rows if r[4]]
        if people:
            who = [key("person", r[2]) for r in people]
            p.frame("person", pl.DataFrame({"id": who}))
            p.frame(
                "name",
                pl.DataFrame(
                    {
                        "id": [p.id("name", w, "ro", "official") for w in who],
                        "entity_id": who,
                        "text": [(r[4] or "").title() for r in people],
                        "lang": "ro",
                        "kind": "official",
                        "is_preferred": True,
                    }
                ),
            )
            p.frame(
                "candidacy",
                pl.DataFrame(
                    {
                        "id": [key("candidacy", r[2]) for r in people],
                        "person_id": who,
                        "option_id": [r[2] for r in people],
                        "role": "head",
                        "ballot_name": [(r[4] or "").title() for r in people],
                        "is_independent": True,
                    }
                ),
            )
        sets = pl.DataFrame({"contest": rows_contest, "result_set_id": set_ids})
        stage_values(p, sets, part, options)
        built[tip] = {r[0]: set() for r in option_rows}
        for r in option_rows:
            built[tip][r[0]].add(r[2])
    if not second:
        declare_councils(p, built)


def declare_councils(p: Package, built: dict[str, dict[str, set[str]]]) -> None:
    """County totals and seats for the county councils and the General Council of Bucharest, as the Authority prints."""
    frame = csv("2020-cj")
    columns = frame.columns
    stems = sorted({c.rsplit("-voturi", 1)[0] for c in columns if c.endswith("-voturi")})
    for row in frame.iter_rows(named=True):
        county = row["county_nce"]
        tip, scope = ("CGMB", "all") if county == "42" else ("CJ", county)
        contest = f"local2020-{tip}-{scope}"
        result_set = key("result_set", contest)
        known = built[tip].get(contest, set())
        for measure, column in ((REGISTERED, "a"), (CAST, "b"), (VALID, "c"), (INVALID, "d")):
            value = number(row[column])
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for stem in stems:
            votes = number(row[f"{stem}-voturi"])
            if votes is None:
                continue
            option = key("option", contest, canon(stem))
            if option not in known:
                option = key("option", contest, canon(f"CANDIDAT INDEPENDENT-{stem}"))
            if option not in known:
                if not votes:
                    continue
                raise ValueError(f"{contest}: declared votes for {stem} without a ballot line")
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
            seats = (number(row.get(f"{stem}-mandate-faza-1")) or 0) + (number(row.get(f"{stem}-mandate-faza-2")) or 0)
            if seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
