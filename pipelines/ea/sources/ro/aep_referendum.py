"""Romanian national referendums of 2012, 2018, and 2019, by polling station.

The Permanent Electoral Authority publishes each referendum as one archive in its electoral database page: a legacy
spreadsheet for the 29 July 2012 vote on removing the President, a spreadsheet for the 6 and 7 October 2018 vote on the
constitutional revision, and one table per question for the 26 May 2019 consultative vote on two justice questions.
Each row is a polling station, in Romania or abroad, with the voters on the lists, the voters who took part, and the
votes for DA, for NU, and the null votes. The 2019 archive prints Romania and abroad as separate tables for each
question, and the 2012 and 2018 archives print them together.
"""

import io
import zipfile
from dataclasses import dataclass
from datetime import date

import fastexcel
import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.ro import aep
from ea.sources.ro.aep import AEP_SOURCE, ROMANIA, SOURCE, VOTES, key

HOST = "https://www.roaep.ro/prezentare/wp-content/uploads/2025/02"
URLS = {
    "2012": f"{HOST}/ref_iun_2012.zip",
    "2018": f"{HOST}/Referendum-pentru-modificarea-Constitutiei-2018.zip",
    "2019": f"{HOST}/Referendum-26-mai-2019.zip",
}
REGISTRY = "ro.aep/ref-registry"
SYSTEM = key("electoral_system", "referendum")
QUOTA = {
    "2012": "50% of the voters on the lists",
    "2018": "30% of the voters on the lists",
}


@dataclass(frozen=True)
class Question:
    slug: str
    english: str
    romanian: str
    type: str


@dataclass(frozen=True)
class Day:
    day: str
    first: date
    last: date
    english: str
    romanian: str
    slug: str
    questions: tuple[Question, ...]
    note: str


DAYS = {
    "2012": Day(
        "2012-07-29",
        date(2012, 7, 29),
        date(2012, 7, 29),
        "2012 Romanian referendum on removing the President",
        "Referendumul național din 29 iulie 2012 pentru demiterea Președintelui României",
        "ro/2012-07-29-referendum",
        (
            Question(
                "removal-of-the-president",
                "Removal of the President of Romania, Traian Băsescu",
                "Sunteți de acord cu demiterea Președintelui României, domnul Traian Băsescu?",
                "mandatory_referendum",
            ),
        ),
        "The Parliament suspended the President on 6 July 2012, and the Constitution then required a referendum on "
        "removing him. Turnout was below the half of the voters on the lists that the law then required for the vote "
        "to be valid, and the Constitutional Court did not validate it.",
    ),
    "2018": Day(
        "2018-10-06",
        date(2018, 10, 6),
        date(2018, 10, 7),
        "2018 Romanian constitutional referendum",
        "Referendumul național din 6–7 octombrie 2018 pentru revizuirea Constituției",
        "ro/2018-10-06-referendum",
        (
            Question(
                "family",
                "Revision of the Constitution to define the family as a union of a man and a woman",
                "Sunteți de acord cu Legea de revizuire a Constituției României în forma adoptată de Parlament?",
                "mandatory_referendum",
            ),
        ),
        "A revision of the Constitution takes effect only when voters approve it in a referendum. The vote ran on two "
        "days. Turnout was below the threshold of a third of the voters on the lists that the law then set, so the "
        "referendum was not valid.",
    ),
    "2019": Day(
        "2019-05-26",
        date(2019, 5, 26),
        date(2019, 5, 26),
        "2019 Romanian consultative referendum on justice",
        "Referendumul național consultativ din 26 mai 2019 în domeniul justiției",
        "ro/2019-05-26-referendum",
        (
            Question(
                "amnesty-and-pardon",
                "Ban on amnesty and pardon for corruption offenses",
                "Sunteți de acord cu interzicerea amnistiei și grațierii pentru infracțiuni de corupție?",
                "referendum",
            ),
            Question(
                "emergency-ordinances",
                "Ban on emergency ordinances in criminal and judicial matters, and direct challenges to the "
                "Constitutional Court",
                "Sunteți de acord cu interzicerea adoptării de către Guvern a ordonanțelor de urgență în domeniul "
                "infracțiunilor, pedepselor și organizării judiciare și cu extinderea dreptului de a ataca "
                "ordonanțele direct la Curtea Constituțională?",
                "referendum",
            ),
        ),
        "The President called a consultative referendum, held on the day of the European Parliament election. The "
        "two questions were put on separate ballots.",
    ),
}
FIX = str.maketrans({"\x93": '"', "\x94": '"', "\x96": "-", "\x92": "'", "\x91": "'"})


def package_key(year: str) -> str:
    return f"ro.aep/ref-{DAYS[year].day}"


def packages() -> list[str]:
    return [REGISTRY, *(package_key(year) for year in DAYS)]


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def archive(year: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, URLS[year]).path.read_bytes()))


def clean(text: object) -> str:
    return " ".join(str(text or "").translate(FIX).split())


def counts(frame: pl.DataFrame) -> pl.DataFrame:
    """Add the accounting columns every table shares: valid is DA plus NU, and a list of zero is no list."""
    return frame.with_columns(
        (pl.col("da") + pl.col("nu")).alias("valid"),
        pl.when(pl.col("registered") > 0).then(pl.col("registered")).otherwise(None).alias("registered"),
    )


def table_2012() -> pl.DataFrame:
    with archive("2012") as z:
        book = fastexcel.read_excel(z.read("sv_date.xls"))
    frame = book.load_sheet(0).to_polars()
    rows = []
    for r in frame.iter_rows(named=True):
        abroad = int(r["circ_jud"]) == 43
        where = (
            [clean(r["tara"]), clean(r["denloc"]), clean(r["adresa"])]
            if abroad
            else [clean(r["denloc"]), clean(r["adresa"])]
        )
        rows.append(
            {
                "circ": int(r["circ_jud"]),
                "cname": clean(r["den_jud"]),
                "nr": str(int(r["sv"])),
                "place": " — ".join(w for w in where if w),
                "postal": False,
                "registered": int(r["ta"]),
                "cast": int(r["tapu"]),
                "invalid": int(r["tvn"]),
                "da": int(r["vveda"]),
                "nu": int(r["vvenu"]),
            }
        )
    return counts(pl.DataFrame(rows))


def table_2018() -> pl.DataFrame:
    with archive("2018") as z:
        book = fastexcel.read_excel(z.read("rezultate_referendum_2018.xlsx"))
    frame = book.load_sheet_by_name("rezultate").to_polars()
    rows = []
    for r in frame.iter_rows(named=True):
        where = [clean(r["UAT"]), clean(r["denloc"]), clean(r["institutie"])]
        if int(r["circ"]) == 43:
            where = [clean(r["tara"]), *where]
        rows.append(
            {
                "circ": int(r["circ"]),
                "cname": clean(r["numecirc"]),
                "nr": str(int(r["sv"])),
                "place": " — ".join(w for w in where if w),
                "postal": False,
                "registered": int(r["tal"]),
                "cast": int(r["tapu"]),
                "invalid": int(r["tvn"]),
                "da": int(r["vveda"]),
                "nu": int(r["vvenu"]),
            }
        )
    return counts(pl.DataFrame(rows))


def table_2019(question: int) -> pl.DataFrame:
    parts = []
    with archive("2019") as z:
        for where in ("Romania", "strainatate"):
            data = z.read(f"Rezultate_{where}_intrebarea_{question}.csv")
            frame = pl.read_csv(data, infer_schema_length=0).with_columns(
                pl.col("Cod birou electoral").cast(pl.Int64).alias("circ")
            )
            parts.append(
                frame.select(
                    "circ",
                    pl.col("Județ").alias("cname"),
                    pl.col("Nr").str.strip_chars().alias("nr"),
                    pl.concat_str(["Uat", "Localitate", "Secție"], separator=" — ", ignore_nulls=True).alias("place"),
                    pl.lit(False).alias("postal"),
                    pl.col("1").cast(pl.Int64).alias("registered"),
                    pl.col("2").cast(pl.Int64).alias("cast"),
                    pl.col("7").cast(pl.Int64).alias("invalid"),
                    pl.col("5").cast(pl.Int64).alias("da"),
                    pl.col("6").cast(pl.Int64).alias("nu"),
                )
            )
    return counts(pl.concat(parts).with_columns(pl.col("place").map_elements(clean, return_dtype=pl.String)))


def table(year: str, question: int) -> pl.DataFrame:
    return {"2012": table_2012, "2018": table_2018}[year]() if year != "2019" else table_2019(question)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The electoral system of Romanian national referendums, from the Permanent Electoral Authority's data",
        depends=["ro.aep/registry"],
    )
    p.external.add(ROMANIA)
    p.add("electoral_system", id=SYSTEM, polity_id=ROMANIA, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Voters answer DA (yes) or NU (no), and the answer with the most valid votes carries. Whether the "
        "result binds depends on the kind of referendum and on a turnout threshold set by law, which is not "
        "modeled here.",
        lang="en",
    )
    return p


def build(year: str) -> Package:
    d = DAYS[year]
    p = Package(
        package_key(year),
        SOURCE,
        f"The {d.english} by polling station, from the Permanent Electoral Authority's open data",
        depends=["ro.aep/registry", REGISTRY],
    )
    p.external.add(ROMANIA)
    p.artifact(raw.cached(SOURCE, URLS[year]), AEP_SOURCE)
    election = key("election", d.day)
    p.add(
        "election",
        id=election,
        polity_id=ROMANIA,
        kind="referendum",
        date_from=d.first,
        date_to=d.last,
        status="held",
    )
    p.name(election, d.english, "en", "official")
    p.name(election, d.romanian, "ro", "native", False)
    p.add("slug", path=d.slug, entity_id=election, is_current=True)
    p.add(
        "note",
        id=key("note", election),
        entity_id=election,
        kind="context",
        body_md=d.note,
        lang="en",
    )
    units: pl.DataFrame | None = None
    for position, question in enumerate(d.questions, start=1):
        frame = table(year, position)
        if units is None:
            frame = aep.unit_rows(p, frame, d.day)
            units = frame.select("unit", "circ", "nr")
        else:
            frame = aep.unit_rows(p, frame, d.day, add=False)
            if set(frame["unit"]) != set(units["unit"]):
                raise ValueError(f"{d.day}: question {position} has different polling stations than question 1")
        contest = key("contest", d.day, position)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type=question.type)
        p.name(contest, question.english, "en", "official")
        p.name(contest, question.romanian, "ro", "native", False)
        p.add("slug", path=f"{d.slug}/{question.slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", d.day, position)
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
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        options: dict[str, str] = {}
        for number, (column, label, english) in enumerate((("da", "DA", "Yes"), ("nu", "NU", "No")), start=1):
            option = options[column] = key("option", d.day, position, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=number,
                ballot_label=label,
            )
            p.name(option, english, "en", "official")
            p.name(option, label, "ro", "native", False)
        aep.values(p, result_set, frame.with_columns(pl.lit(None, pl.Int64).alias("blank")), options)
    p.accept(
        "accounting.turnout",
        "some polling stations print more voters than registered voters on the lists, mostly abroad and on special "
        "lists; stored as printed",
    )
    p.accept(
        "accounting.ballots_sum",
        "some polling stations print valid and null ballots that do not add up to the voters who took part; stored "
        "as printed",
    )
    return p


METHOD = (
    "Counted per polling station and published by the Permanent Electoral Authority as open data. Registered "
    "voters are the voters on the lists, and abroad, where no list is kept, they are left empty. Ballots cast are "
    "the voters who took part. Valid votes are the votes for DA and NU, and invalid ballots are the null votes. "
    "Contested votes, complaints, and the ballots received and left unused are not stored."
)


def parse() -> None:
    registry().write()
    for year in DAYS:
        build(year).write()


__all__ = ["VOTES", "build", "fetch", "packages", "parse", "registry"]
