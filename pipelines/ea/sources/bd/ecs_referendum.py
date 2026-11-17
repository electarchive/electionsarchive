"""Bangladesh's national referendum of 12 February 2026 on the July National Charter, from the Election Commission."""

from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "bd.ecs_referendum"
BANGLADESH = str(uuid5(package.NAMESPACE, "polity\x1fBD"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBGD"))
DAY = date(2026, 2, 12)
ASSET = "https://asset.ecs.gov.bd/media/uploaded_files/notices/2026/"
# The gazette of 13 February 2026 and the correction of 25 February 2026 that replaced its figures.
GAZETTE = ASSET + "02/11a7bdd8-28ab-4fe9-b85f-f29b792febae.pdf"
CORRECTION = ASSET + "03/34802740-4921-46a5-acb2-f316f463e4f3.pdf"
RESULT_PAGE = "https://web.archive.org/web/20260729072755id_/http://103.183.38.66:81/gonovote/result"
FILES = {GAZETTE: ".pdf", CORRECTION: ".pdf", RESULT_PAGE: ".html"}

# The corrected gazette notice of 25 February 2026, which supersedes the figures of 13 February (48,200,660 yes,
# 22,071,726 no): yes, no, valid, rejected, and total ballots cast. The Commission's result page prints the same
# yes and no counts with the electoral roll and the ballots cast.
YES, NO, VALID, REJECTED, CAST = 47_225_980, 21_960_231, 69_186_211, 7_435_196, 76_621_407
REGISTERED = 127_298_585

METHOD = (
    "National totals of the referendum held together with the thirteenth Jatiya Sangsad election, as the Bangladesh "
    "Election Commission declared them under section 16(3) of the Referendum Ordinance 2025. The Commission "
    "gazetted the result on 13 February 2026 and replaced every figure in a correction of 25 February 2026; this "
    "package holds the corrected figures, which the Commission's result page also prints, with the electoral roll "
    "from that page. The Commission published no result by district or constituency in the files the archive could "
    "reach. Valid ballots are the yes and no votes together; ballots cast are valid and rejected ballots together."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY}"]


def fetch() -> None:
    for url, suffix in FILES.items():
        raw.fetch(SOURCE, url, suffix=suffix)


def parse() -> None:
    if YES + NO != VALID or VALID + REJECTED != CAST:
        raise ValueError("the declared counts do not add up")
    build().write()


def emit() -> None:
    package.run("load", packages())


def build() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "Bangladesh's national referendum of 12 February 2026 on the July National Charter, national totals from "
        "the Election Commission",
    )
    p.external.add(BANGLADESH)
    p.external.add(COUNTRY)
    source = key("source", "ecs")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Bangladesh Election Commission",
        title="Gazette notice of the 2026 referendum result, with its correction",
        url="https://www.ecs.gov.bd/",
        license="No license stated; published by the electoral authority for public use",
        attribution="Bangladesh Election Commission",
    )
    for url in FILES:
        p.artifact(raw.cached(SOURCE, url), source)

    election = p.id("election", DAY)
    p.add(
        "election",
        id=election,
        polity_id=BANGLADESH,
        kind="referendum",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election, "2026 Bangladeshi referendum on the July National Charter", "en", "official")
    p.name(election, "জুলাই জাতীয় সনদ গণভোট ২০২৬", "bn", "native", False)
    base = f"bd/{DAY}-referendum"
    p.add("slug", path=base, entity_id=election, is_current=True)

    system = key("electoral_system", "referendum")
    p.add("electoral_system", id=system, polity_id=BANGLADESH, rules={"formula": "referendum"}, rules_version=1)
    contest = p.id("contest", DAY)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        electoral_system_id=system,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(
        contest,
        "Whether the people consent to the constitutional reform proposals of the July National Charter",
        "en",
        "official",
    )
    p.add("slug", path=f"{base}/question", entity_id=contest, is_current=True)
    options = []
    for position, label in enumerate(("Yes", "No"), 1):
        option = p.id("option", DAY, position)
        options.append(option)
        p.add(
            "contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=label
        )

    result_set = p.id("result_set", DAY)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won")

    p.add(
        "scheme", code="bd.nation", polity_id=BANGLADESH, kind="administrative", rank=1, noun="nation", code_system=None
    )
    unit = p.id("unit", "national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=BANGLADESH,
        scheme_code="bd.nation",
        code="BD",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    rows = [
        (unit, options[0], 1, YES),
        (unit, options[1], 1, NO),
        (unit, None, 2, REGISTERED),
        (unit, None, 3, CAST),
        (unit, None, 4, VALID),
        (unit, None, 5, REJECTED),
    ]
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
