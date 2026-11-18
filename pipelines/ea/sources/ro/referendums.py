"""Romania's national referendums of 2018 and 2019, from the Central Electoral Bureau and the Permanent Electoral Authority.

The referendum of 26 May 2019 (two questions) is read by polling station from the CSV files of the Authority's results
portal, which cover every station in Romania but none abroad. The referendum of 6 and 7 October 2018 is read from the
signed records of the county bureaus and of the Bucharest sectors, which the Central Electoral Bureau publishes as
scans; their figures were read from the page images, each with its page and box, and the counts add up to the Bureau's
national record to the vote. The portal and the Bureau refuse scripted requests, so the files were saved from a
browser and stored with `raw.store`."""

import json
from datetime import date
from pathlib import Path
from typing import TypedDict
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "ro.referendums"
ROMANIA = str(uuid5(package.NAMESPACE, "polity\x1fRO"))
SITE = "https://prezenta.roaep.ro/referendum26052019/data/pv/csv"
BROWSER = "browser://20261118-192429-7b088d/"
CSV = {1: f"{SITE}/pv_RO_REF1_FINAL.csv", 2: f"{SITE}/pv_RO_REF2_FINAL.csv"}
BEC_2019 = {1: f"{BROWSER}BEC_R1_sgn_GS_P.pdf", 2: f"{BROWSER}BEC_R2_sgn_GS_P.pdf"}
BEC_2018 = f"{BROWSER}pv_final_bec.pdf"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
STATIONS, CIRCUMSCRIPTIONS = "ro.sv", "ro.circumscription"
FOLD = str.maketrans({"Ş": "Ș", "ş": "ș", "Ţ": "Ț", "ţ": "ț"})
DAYS = {"2018": date(2018, 10, 6), "2019": date(2019, 5, 26)}

# The Central Electoral Bureau's national records: registered, participants, yes, no, null, as printed (item 1, 2, 5, 6, 7).
NATIONAL_2018 = (18_279_011, 3_857_308, 3_531_732, 249_412, 76_111)
NATIONAL_2019 = {
    1: (18_277_511, 7_922_591, 6_459_383, 1_059_678, 403_530),
    2: (18_278_290, 7_923_869, 6_477_865, 1_038_916, 407_088),
}

COUNTIES = {
    "AB": "Alba", "AR": "Arad", "AG": "Argeș", "BC": "Bacău", "BH": "Bihor", "BN": "Bistrița-Năsăud",
    "BT": "Botoșani", "BV": "Brașov", "BR": "Brăila", "BZ": "Buzău", "CS": "Caraș-Severin", "CL": "Călărași",
    "CJ": "Cluj", "CT": "Constanța", "CV": "Covasna", "DB": "Dâmbovița", "DJ": "Dolj", "GL": "Galați",
    "GR": "Giurgiu", "GJ": "Gorj", "HR": "Harghita", "HD": "Hunedoara", "IL": "Ialomița", "IS": "Iași",
    "IF": "Ilfov", "MM": "Maramureș", "MH": "Mehedinți", "MS": "Mureș", "NT": "Neamț", "OT": "Olt",
    "PH": "Prahova", "SM": "Satu Mare", "SJ": "Sălaj", "SB": "Sibiu", "SV": "Suceava", "TR": "Teleorman",
    "TM": "Timiș", "TL": "Tulcea", "VS": "Vaslui", "VL": "Vâlcea", "VN": "Vrancea",
}  # fmt: skip
SECTORS = {f"B{n}": f"București, sectorul {n}" for n in range(1, 7)}
ABROAD = "DIASPORA"

METHOD_2019 = (
    "Counted per polling station and published by the Permanent Electoral Authority on its results portal, from "
    "each station's signed record. Registered voters are the persons on the lists for the referendum, ballots cast "
    "are the participants, yes and no are the valid votes for each answer, and invalid ballots are the null votes; "
    "valid votes are the yes and no votes together. The portal's files cover every polling station in Romania and "
    "none abroad, so the votes of Romanians abroad are not in this result set; the Central Electoral Bureau's "
    "national record, which includes them, prints {national}. Registered voters abroad are not counted, because "
    "stations abroad have no permanent list."
)
METHOD_2018 = (
    "Counted per county bureau, per sector of Bucharest, and for the polling stations abroad together, from the "
    "signed record of each bureau, which the Central Electoral Bureau publishes as a scan. The figures were read "
    "from the page images, and each keeps its page and box. Registered voters are the persons on the lists for the "
    "referendum, ballots cast are the participants, yes and no are the valid votes for each answer, and invalid "
    "ballots are the null votes; valid votes are the yes and no votes together. The sum over every unit equals the "
    "Central Electoral Bureau's national record to the vote. The bureaus abroad print no registered voters. The "
    "record of the municipality of Bucharest is not stored, because it only adds its six sectors."
)
CAVEAT_2018 = (
    "Under Romanian law a national referendum is valid only when at least 30% of the persons on the lists take "
    "part. About 21% did, so the vote was invalid and the revision was not adopted, although the yes votes were the "
    "large majority of the valid votes."
)
ACCEPTED = (
    "accounting.ballots_sum",
    (
        "some records print participants above the valid and null votes together, which the Bureau's form allows "
        "(the participants must be at least their sum); stored as printed"
    ),
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def tidy(text: object) -> str:
    return " ".join(str(text or "").translate(FOLD).split())


def title(text: object) -> str:
    return tidy(text).title()


def fetch() -> None:
    """The portal's CSV files are fetched here; the Bureau's scans were saved from a browser and stored with `raw.store`."""
    for url in CSV.values():
        raw.fetch(SOURCE, url, suffix=".csv")


def packages() -> list[str]:
    return [f"{SOURCE}/{year}" for year in DAYS]


def source(p: Package, name: str, publisher: str, title_: str, url: str) -> str:
    id = key("source", name)
    p.add(
        "source",
        id=id,
        kind="official_body",
        publisher=publisher,
        title=title_,
        url=url,
        license="No license stated; published by the electoral authority for public use",
        attribution=publisher,
    )
    return id


def stage_election(p: Package, day: date, english: str, romanian: str, slug: str) -> tuple[str, str]:
    """The election, and the referendum's electoral system, which every question shares."""
    election = p.id("election", day)
    p.add(
        "election",
        id=election,
        polity_id=ROMANIA,
        kind="referendum",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election, english, "en", "official")
    p.name(election, romanian, "ro", "native", False)
    p.add("slug", path=slug, entity_id=election, is_current=True)
    system = key("electoral_system", "referendum")
    p.add("electoral_system", id=system, polity_id=ROMANIA, rules={"formula": "referendum"}, rules_version=1)
    return election, system


class Question:
    """One ballot question: its contest, its two answers, and its result set."""

    def __init__(self, p: Package, day: date, election: str, system: str, number: int, **text: str) -> None:
        self.p = p
        self.number = number
        self.contest = p.id("contest", day, number)
        p.add(
            "contest",
            id=self.contest,
            election_id=election,
            kind="ballot_question",
            electoral_system_id=system,
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=self.contest, type=text["type"])
        p.name(self.contest, text["english"], "en", "official")
        p.name(self.contest, text["romanian"], "ro", "native", False)
        p.add("slug", path=text["slug"], entity_id=self.contest, is_current=True)
        self.yes = p.id("option", day, number, "yes")
        self.no = p.id("option", day, number, "no")
        for option, position, label in ((self.yes, 1, "Yes"), (self.no, 2, "No")):
            p.add(
                "contest_option",
                id=option,
                contest_id=self.contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        self.result_set = p.id("result_set", day, number)
        p.add(
            "result_set",
            id=self.result_set,
            election_id=election,
            contest_id=self.contest,
            stage="official",
            kind="counted",
            source_id=text["source"],
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=p.id("note", self.result_set, "methodology"),
            entity_id=self.result_set,
            kind="methodology",
            body_md=text["method"],
            lang="en",
        )
        self.values: list[tuple[str, str | None, int, int, int]] = []

    def record(
        self, unit: str, registered: int | None, cast: int, yes: int, no: int, invalid: int, flags: int = 0
    ) -> None:
        for option, measure, value in (
            (None, REGISTERED, registered),
            (None, CAST, cast),
            (None, VALID, yes + no),
            (None, INVALID, invalid),
            (self.yes, VOTES, yes),
            (self.no, VOTES, no),
        ):
            if value is not None:
                self.values.append((unit, option, measure, value, flags))

    def stage(self) -> None:
        self.p.frame(
            "result_value",
            pl.DataFrame(
                self.values,
                schema={
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                    "flags": pl.Int16,
                },
                orient="row",
            ).with_columns(
                result_set_id=pl.lit(self.result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
            ),
        )


def units(p: Package, scheme: str, rows: list[tuple[str, str, str]]) -> None:
    """Reporting units as (id, code, name) rows; their places are not known, so none is linked."""
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": [r[0] for r in rows],
                "polity_id": ROMANIA,
                "scheme_code": scheme,
                "code": [r[1] for r in rows],
                "area_relation": "unlocated",
                "kind": "standard" if scheme == STATIONS else "aggregate",
            }
        ),
    )
    p.frame(
        "name",
        pl.DataFrame(
            {
                "id": [p.id("name", r[0], "ro", "official", r[2]) for r in rows],
                "entity_id": [r[0] for r in rows],
                "text": [r[2] for r in rows],
                "lang": "ro",
                "kind": "official",
                "is_preferred": True,
            }
        ),
    )


# 26 May 2019


def stations(question: int) -> pl.DataFrame:
    frame = pl.read_csv(raw.cached(SOURCE, CSV[question]).path, encoding="utf8-lossy", infer_schema=False)
    numbers = [str(n) for n in range(1, 8)]
    return frame.with_columns(pl.col(n).cast(pl.Int64) for n in numbers)


def referendum_2019() -> Package:
    day = DAYS["2019"]
    p = Package(
        f"{SOURCE}/2019",
        SOURCE,
        "Romania's national referendum of 26 May 2019 on two questions, by polling station, from the Permanent "
        "Electoral Authority's results portal",
    )
    p.external.add(ROMANIA)
    p.external.add(STATIONS)
    aep = source(
        p,
        "aep",
        "Autoritatea Electorală Permanentă",
        "Results of the referendum of 26 May 2019 by polling station",
        "https://prezenta.roaep.ro/referendum26052019/",
    )
    bec = source(
        p,
        "bec",
        "Biroul Electoral Central",
        "Records of the Central Electoral Bureau on the results of the referendum of 26 May 2019",
        "https://www.bec.ro/",
    )
    for url in CSV.values():
        p.artifact(raw.cached(SOURCE, url), aep)
    for url in BEC_2019.values():
        p.artifact(raw.cached(SOURCE, url), bec)
    election, system = stage_election(
        p,
        day,
        "2019 Romanian national referendum",
        "Referendumul național din 26 mai 2019",
        "ro/2019-05-26-referendum",
    )
    texts = {
        1: (
            "Whether to ban amnesty and pardon for corruption offenses",
            "Interzicerea amnistiei și grațierii pentru infracțiuni de corupție",
        ),
        2: (
            (
                "Whether to bar the Government from adopting emergency ordinances on offenses, penalties, and the "
                "organization of the judiciary, and to let other constitutional authorities refer ordinances directly "
                "to the Constitutional Court"
            ),
            (
                "Interzicerea adoptării de către Guvern a ordonanțelor de urgență în domeniul infracțiunilor, "
                "pedepselor și al organizării judiciare și extinderea dreptului altor autorități constituționale de a "
                "sesiza direct Curtea Constituțională cu privire la ordonanțe"
            ),
        ),
    }
    questions: dict[int, Question] = {}
    frames = {n: stations(n) for n in (1, 2)}
    rows: dict[str, str] = {}
    for number in (1, 2):
        registered, cast, yes, no, null = NATIONAL_2019[number]
        total = (
            f"{registered:,} registered voters, {cast:,} participants, {yes:,} yes, {no:,} no, and {null:,} null votes"
        )
        questions[number] = Question(
            p,
            day,
            election,
            system,
            number,
            type="referendum",
            english=texts[number][0],
            romanian=texts[number][1],
            slug=f"ro/2019-05-26-referendum/question-{number}",
            source=aep,
            method=METHOD_2019.format(national=total),
        )
        for row in frames[number].iter_rows(named=True):
            code = f"{day}:{row['Cod birou electoral']}/{tidy(row['Nr'])}"
            if code not in rows:
                where = title(row["Localitate"])
                station = tidy(row["Secție"])
                rows[code] = f"{station} ({where}, station {tidy(row['Nr'])})" if station else f"{where}, {row['Nr']}"
            questions[number].record(key("unit", code), row["1"], row["2"], row["5"], row["6"], row["7"])
    units(p, STATIONS, [(key("unit", code), code, text) for code, text in rows.items()])
    for question in questions.values():
        question.stage()
    p.accept(
        "accounting.turnout",
        "some stations, mostly those voting on supplementary and special lists, print more participants than the "
        "persons on their lists; stored as printed",
    )
    return p


# 6 and 7 October 2018


class Read(TypedDict):
    page: list[int]
    values: list[int | None]
    boxes: list[list[float] | None]


def digitized_2018() -> dict[str, Read]:
    found: dict[str, Read] = json.loads(Path(__file__).with_name("referendums_2018.json").read_text())
    return found


def referendum_2018() -> Package:
    day = DAYS["2018"]
    p = Package(
        f"{SOURCE}/2018",
        SOURCE,
        "Romania's national referendum of 6 and 7 October 2018 on the revision of the Constitution, by county, from "
        "the records of the Central Electoral Bureau",
    )
    p.external.add(ROMANIA)
    p.external.add(CIRCUMSCRIPTIONS)
    bec = source(
        p,
        "bec",
        "Biroul Electoral Central",
        "Records of the county bureaus and of the Central Electoral Bureau on the referendum of 6 and 7 October 2018",
        "https://referendum2018.bec.ro/",
    )
    artifacts: dict[str, str] = {}
    scans = {code: f"{BROWSER}{code.lower()}.pdf" for code in (*COUNTIES, *SECTORS)}
    scans[ABROAD] = f"{BROWSER}diaspora.pdf"
    for code, url in scans.items():
        artifact = raw.cached(SOURCE, url)
        artifacts[code] = p.artifact(artifact, bec)
    p.artifact(raw.cached(SOURCE, BEC_2018), bec)
    election, system = stage_election(
        p,
        day,
        "2018 Romanian constitutional referendum",
        "Referendumul național pentru revizuirea Constituției din 6 și 7 octombrie 2018",
        "ro/2018-10-06-referendum",
    )
    p.add(
        "note",
        id=p.id("note", election, "threshold"),
        entity_id=election,
        kind="caveat",
        body_md=CAVEAT_2018,
        lang="en",
    )
    question = Question(
        p,
        day,
        election,
        system,
        1,
        type="mandatory_referendum",
        english="Whether to approve the law revising the Constitution as Parliament adopted it",
        romanian="Revizuirea Constituției României în forma adoptată de Parlament",
        slug="ro/2018-10-06-referendum/question",
        source=bec,
        method=METHOD_2018,
    )
    data = digitized_2018()
    names = {**{code: f"Județul {name}" for code, name in COUNTIES.items()}, **SECTORS}
    names[ABROAD] = "Secțiile de votare din străinătate"
    rows: list[tuple[str, str, str]] = []
    pages: list[dict[str, object]] = []
    locators: list[tuple[str, str | None, int, str, float, float, float, float]] = []
    totals = [0] * 5
    for code, name in names.items():
        read = data[code.lower()]
        registered, cast, _received, _unused, yes, no, null = read["values"]
        assert isinstance(cast, int) and isinstance(yes, int) and isinstance(no, int) and isinstance(null, int)
        assert cast >= yes + no + null
        unit = key("unit", f"{day}:{code}")
        rows.append((unit, f"{day}:{code}", name))
        page = key("page", code)
        width, height = read["page"]
        pages.append(
            {
                "id": page,
                "artifact_id": artifacts[code],
                "page_number": 1,
                "image_key": f"pages/{SOURCE}/{raw.cached(SOURCE, scans[code]).sha256}-1.png",
                "width": width,
                "height": height,
            }
        )
        registered_value = registered or None
        question.record(unit, registered_value, cast, yes, no, null, DIGITIZED)
        boxes = read["boxes"]
        for index, measure, option, value in (
            (0, REGISTERED, None, registered_value),
            (1, CAST, None, cast),
            (4, VOTES, question.yes, yes),
            (5, VOTES, question.no, no),
            (6, INVALID, None, null),
        ):
            box = boxes[index]
            if value is not None and box:
                locators.append((unit, option, measure, page, box[0], box[1], box[2], box[3]))
        for i, value in enumerate((registered, cast, yes, no, null)):
            totals[i] += value or 0
    if tuple(totals) != NATIONAL_2018:
        raise ValueError(f"the records add up to {totals}, not to the national record {NATIONAL_2018}")
    units(p, CIRCUMSCRIPTIONS, rows)
    question.stage()
    p.frame("source_page", pl.DataFrame(pages, schema_overrides={"page_number": pl.Int32}))
    result_set = question.result_set
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
            orient="row",
            infer_schema_length=None,
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    registered, cast, yes, no, null = NATIONAL_2018
    p.frame(
        "declared",
        pl.DataFrame(
            [
                (result_set, None, REGISTERED, registered),
                (result_set, None, CAST, cast),
                (result_set, None, INVALID, null),
                (result_set, question.yes, VOTES, yes),
                (result_set, question.no, VOTES, no),
            ],
            schema={"result_set_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
            orient="row",
        ),
    )
    p.accept(*ACCEPTED)
    return p


def parse() -> None:
    referendum_2018().write()
    referendum_2019().write()


def emit() -> None:
    package.run("load", packages())
