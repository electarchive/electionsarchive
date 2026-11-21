"""Poland's national referendum of 29 November 1987, from the Central Referendum Commission's notice.

The Central Referendum Commission (Centralna Komisja do Spraw Referendum) published the result in Monitor Polski 1987
nr 34 poz. 294 (notice of 30 November 1987), a 16-page scan with a noisy text layer: the national totals for the two
questions (page 2), a table of the persons entitled to vote, the voters, and the valid ballots in each of the 49
voivodeships (page 3), and the answers to each question in each voivodeship (pages 4 to 16). No other canvass of the
vote is online.

The figures were read from the page images against the text layer, and every voivodeship's three answers to a question
add up to its valid ballots, the voivodeships add up to the Commission's national totals, and the three parts of each
question add up to the valid ballots, all to the vote. Each figure keeps its page and the box of the printed number
where the text layer locates one. The answers of three voivodeships (chełmskie, ciechanowskie, and częstochowskie, on
page 5) and a few single figures are missing from the text layer, so they were read from the page image without a
box.

There are no People's Republic voivodeship areas in the archive, so the 49 voivodeships of the division of 1975 are
reporting units with no area, in their own scheme with dated codes; the national unit has a dated code of its own and
is equal to the country."""

import json
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import NAMESPACE, Package
from ea.sources.pl.wojewodztwo_1975 import VOIVODESHIPS
from ea.store import DATA

SOURCE = "pl.kbw_referendum_1987"
GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
ELECTION = f"{SOURCE}/1987"
DAY = date(1987, 11, 29)
POLAND = str(uuid5(NAMESPACE, "polity\x1fPL"))
COUNTRY = str(uuid5(NAMESPACE, "area:world.country@None\x1fPOL"))
NOTICE = "https://api.sejm.gov.pl/eli/acts/MP/1987/294/text.pdf"
PAGE = "https://api.sejm.gov.pl/eli/acts/MP/1987/294"
PAGES = DATA / "pages" / SOURCE
NATION, WOJEWODZTWO = "pl.nation_1987", "pl.wojewodztwo_1987"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
DERIVED, DIGITIZED = 1, 8
DPI = 150

QUESTIONS = (
    (
        "Program of radical economic recovery",
        "Program radykalnego uzdrowienia gospodarki",
        (
            "Czy jesteś za pełną realizacją przedstawionego Sejmowi programu radykalnego uzdrowienia gospodarki, "
            "zmierzającego do wyraźnej poprawy warunków życia społeczeństwa, wiedząc, że wymaga to przejścia przez "
            "trudny dwu-trzyletni okres szybkich zmian?"
        ),
        (
            "Are you in favor of full implementation of the program of radical economic recovery presented to the "
            "Sejm, aimed at a clear improvement in the living conditions of society, knowing that it requires going "
            "through a difficult two- to three-year period of rapid changes?"
        ),
    ),
    (
        "Polish model of deep democratization of political life",
        "Polski model głębokiej demokratyzacji życia politycznego",
        (
            "Czy opowiadasz się za polskim modelem głębokiej demokratyzacji życia politycznego, której celem jest "
            "umocnienie samorządności, rozszerzenie praw obywateli i zwiększenie ich uczestnictwa w rządzeniu krajem?"
        ),
        (
            "Do you support the Polish model of deep democratization of political life, whose aim is to strengthen "
            "self-government, extend citizens' rights, and increase their participation in governing the country?"
        ),
    ),
)
CAVEAT = (
    "Under article 19 of the law of 6 May 1987 on social consultations and referendum, the result was decisive only "
    "if more than half of the persons entitled to vote answered yes (or no). The yes votes were 44.28% of those "
    "entitled on the first question and 46.29% on the second, so the Commission declared the result not decisive on "
    "either question."
)
METHOD = (
    "National totals and the totals of the 49 voivodeships as the Central Referendum Commission's notice of 30 "
    "November 1987 prints them. Registered voters are the persons on the lists when voting ended, ballots cast are "
    "the persons who took part, valid are the yes and no answers together, and blank are the valid ballots that gave "
    "no answer to this question or struck the question out. Invalid ballots are the ballots cast less the valid "
    "ballots of the notice, which count the blank answers; the notice prints them for the whole country only, so the "
    "voivodeships' are derived. The figures were read from the page images, and each keeps its page and the box of "
    "the printed number{boxes}."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source_id() -> str:
    return key("source", "notice")


def fetch() -> None:
    raw.fetch(SOURCE, NOTICE, suffix=".pdf")
    pages()


def pages() -> list[Path]:
    artifact = raw.cached(SOURCE, NOTICE)
    PAGES.mkdir(parents=True, exist_ok=True)
    found = sorted(PAGES.glob(f"{artifact.sha256}-*.png"))
    if not found:
        with tempfile.TemporaryDirectory() as scratch:
            subprocess.run(
                ["pdftoppm", "-r", str(DPI), "-gray", "-png", str(artifact.path), f"{scratch}/p"], check=True
            )
            for image in sorted(Path(scratch).glob("p-*.png")):
                shutil.move(image, PAGES / f"{artifact.sha256}-{int(image.stem.split('-')[1])}.png")
        found = sorted(PAGES.glob(f"{artifact.sha256}-*.png"))
    return sorted(found, key=lambda path: int(path.stem.split("-")[1]))


def read() -> dict[str, object]:
    found: dict[str, object] = json.loads(Path(__file__).with_name("kbw_referendum_1987.json").read_text())
    return found


def unit(code: str) -> str:
    return key("unit", f"{DAY}:{code}")


def packages() -> list[str]:
    return [GEO, REGISTRY, ELECTION]


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Central Referendum Commission's notice on the national referendum of 29 November 1987 as a source, and "
        "the electoral system of the referendum",
    )
    p.external.add(POLAND)
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher="Centralna Komisja do Spraw Referendum",
        title="Obwieszczenie Centralnej Komisji do Spraw Referendum z dnia 30 listopada 1987 r. o wyniku referendum "
        "ogólnokrajowego przeprowadzonego dnia 29 listopada 1987 r. (Monitor Polski 1987 nr 34 poz. 294)",
        url=PAGE,
        license="Official legal texts are not subject to copyright (Prawo autorskie, art. 4)",
        license_url=PAGE,
        attribution="Kancelaria Sejmu, Internetowy System Aktów Prawnych",
        notes="A scan with a noisy text layer; the figures were read from the page images.",
    )
    p.artifact(raw.cached(SOURCE, NOTICE), source_id())
    system = key("electoral_system", "referendum")
    p.add("electoral_system", id=system, polity_id=POLAND, rules={"formula": "referendum"}, rules_version=1)
    p.name(system, "Polish national referendum of 1987", "en", "official")
    p.add(
        "note",
        id=p.id("note", system, "methodology"),
        entity_id=system,
        kind="methodology",
        body_md=(
            "The Sejm put two questions to the voters of the whole country under the law of 6 May 1987 on social "
            "consultations and referendum. Each is answered yes or no, and the result decides a question only if "
            "more than half of the persons entitled to vote gave the same answer."
        ),
        lang="en",
    )
    return p


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The national unit and the 49 voivodeship units of the national referendum of 29 November 1987, with dated "
        "codes and no areas for the voivodeships",
    )
    p.external |= {POLAND, COUNTRY}
    units = read()["units"]
    assert isinstance(units, list) and len(units) == 49
    p.add(
        "scheme", code=NATION, polity_id=POLAND, kind="administrative", rank=1, noun="nation of the referendum of 1987"
    )
    p.add(
        "scheme",
        code=WOJEWODZTWO,
        polity_id=POLAND,
        kind="administrative",
        rank=2,
        noun="voivodeship of the referendum of 1987",
    )
    nation = unit("PL")
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": [nation],
                "polity_id": POLAND,
                "scheme_code": NATION,
                "code": f"{DAY}:PL",
                "area_relation": "equals",
                "area_id": COUNTRY,
                "kind": "aggregate",
            }
        ),
    )
    p.name(nation, "Poland", "en", "official")
    p.name(nation, "Polska", "pl", "native", False)
    rows = []
    for number, row in enumerate(units, 1):
        assert isinstance(row, dict) and row["code"] == f"{number:02d}"
        native, english = VOIVODESHIPS[number]
        assert row["name"].endswith(native), (row["name"], native)
        rows.append((unit(row["code"]), row["code"], f"województwo {row['name']}", english))
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": [r[0] for r in rows],
                "polity_id": POLAND,
                "scheme_code": WOJEWODZTWO,
                "code": [f"{DAY}:{r[1]}" for r in rows],
                "parent_id": nation,
                "area_relation": "unlocated",
                "kind": "aggregate",
            }
        ),
    )
    for id, _code, native, english in rows:
        p.name(id, native, "pl", "official")
        p.name(id, english, "en", "english", False)
    return p


class Values:
    """The values and locators of one result set."""

    def __init__(self, result_set: str) -> None:
        self.result_set = result_set
        self.values: list[tuple[str, str | None, int, int, int]] = []
        self.locators: list[tuple[str, str | None, int, int, list[float]]] = []

    def put(
        self, unit: str, option: str | None, measure: int, value: int, flags: int = 0, cell: list[object] | None = None
    ) -> None:
        self.values.append((unit, option, measure, value, flags))
        if cell is not None and cell[2] is not None:
            page, box = cell[1], cell[2]
            assert isinstance(page, int) and isinstance(box, list)
            self.locators.append((unit, option, measure, page, box))


def value(cell: list[object]) -> int:
    number = cell[0]
    assert isinstance(number, int)
    return number


def election() -> Package:
    p = Package(
        ELECTION,
        SOURCE,
        "Poland's national referendum of 29 November 1987 on two questions, by voivodeship and for the country, from "
        "the Central Referendum Commission's notice",
        depends=[GEO, REGISTRY],
    )
    p.external.add(POLAND)
    data = read()
    sizes = data["pages"]
    national = data["national"]
    units = data["units"]
    assert isinstance(sizes, dict) and isinstance(national, dict) and isinstance(units, list)
    artifact = raw.cached(SOURCE, NOTICE)
    p.uses(artifact)
    artifact_id = str(uuid5(NAMESPACE, f"source_artifact\x1f{source_id()}\x1f{artifact.storage_key}"))
    election = p.id("election", DAY)
    p.add(
        "election",
        id=election,
        polity_id=POLAND,
        kind="referendum",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election, "1987 Polish national referendum", "en", "official")
    p.name(election, "Referendum ogólnokrajowe 1987", "pl", "native", False)
    p.add("slug", path="pl/1987-11-29-referendum", entity_id=election, is_current=True)
    p.add(
        "note",
        id=p.id("note", election, "caveat"),
        entity_id=election,
        kind="caveat",
        body_md=CAVEAT,
        lang="en",
    )
    system = key("electoral_system", "referendum")
    nation = unit("PL")
    nat_q = national["q"]
    assert isinstance(nat_q, list)
    boxless = sum(1 for u in units for q in u["q"] for c in q if c[2] is None)
    method = METHOD.format(boxes=f", except {boxless} voivodeship figures that the text layer lacks" if boxless else "")
    registered, voters, valid_cards, invalid = (national[k] for k in ("registered", "voters", "valid", "invalid"))
    for number, (english, polish, text_pl, text_en) in enumerate(QUESTIONS, 1):
        contest = p.id("contest", DAY, number)
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
        p.name(contest, f"1987 Polish referendum, question {number}: {english}", "en", "official")
        p.name(contest, f"{polish} ({number})", "pl", "native", False)
        p.add("slug", path=f"pl/1987-11-29-referendum/question-{number}", entity_id=contest, is_current=True)
        for lang, body in (("pl", text_pl), ("en", text_en)):
            p.add(
                "ballot_question_text",
                id=p.id("ballot_question_text", contest, lang),
                contest_id=contest,
                kind="text",
                lang=lang,
                body=body,
                url=PAGE,
                source_id=source_id(),
            )
        yes, no = p.id("option", DAY, number, "yes"), p.id("option", DAY, number, "no")
        for option, position, tag, label in ((yes, 1, "TAK", "Yes"), (no, 2, "NIE", "No")):
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=tag,
            )
            p.name(option, label, "en", "official")
            p.name(option, tag, "pl", "native", False)
        sets: dict[str, Values] = {}
        for label, default, level, text in (
            (
                "national",
                True,
                "certified",
                "The Central Referendum Commission's national totals for the whole country. " + method,
            ),
            (
                "voivodeship",
                False,
                "official",
                "The totals of each of the 49 voivodeships, which add up to the Commission's national totals. "
                + method,
            ),
        ):
            result_set = p.id("result_set", DAY, number, label)
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage=level,
                kind="counted",
                source_id=source_id(),
                status="active",
                is_default=default,
                completeness=1.0,
            )
            p.add(
                "note",
                id=p.id("note", result_set, "methodology"),
                entity_id=result_set,
                kind="methodology",
                body_md=text,
                lang="en",
            )
            sets[label] = Values(result_set)
        index = number - 1
        # The country.
        top = sets["national"]
        yes_cell, no_cell, blank_cell = nat_q[index]
        top.put(nation, None, REGISTERED, value(registered), DIGITIZED, registered)
        top.put(nation, None, CAST, value(voters), DIGITIZED, voters)
        top.put(nation, None, VALID, value(yes_cell) + value(no_cell), DERIVED)
        top.put(nation, None, INVALID, value(invalid), DIGITIZED, invalid)
        top.put(nation, None, BLANK, value(blank_cell), DIGITIZED, blank_cell)
        top.put(nation, yes, VOTES, value(yes_cell), DIGITIZED, yes_cell)
        top.put(nation, no, VOTES, value(no_cell), DIGITIZED, no_cell)
        assert value(yes_cell) + value(no_cell) + value(blank_cell) == value(valid_cards)
        assert value(valid_cards) + value(invalid) == value(voters)
        expected = [
            value(registered),
            value(voters),
            value(yes_cell) + value(no_cell),
            value(invalid),
            value(blank_cell),
            value(yes_cell),
            value(no_cell),
        ]
        # The voivodeships.
        low = sets["voivodeship"]
        totals = [0] * 7
        for row in units:
            id = unit(row["code"])
            reg, vot, val = row["registered"], row["voters"], row["valid"]
            yes_cell, no_cell, blank_cell = row["q"][index]
            answers = (value(yes_cell), value(no_cell), value(blank_cell))
            assert sum(answers) == value(val), (row["code"], number)
            low.put(id, None, REGISTERED, value(reg), DIGITIZED, reg)
            low.put(id, None, CAST, value(vot), DIGITIZED, vot)
            low.put(id, None, VALID, answers[0] + answers[1], DERIVED)
            low.put(id, None, INVALID, value(vot) - value(val), DERIVED)
            low.put(id, None, BLANK, answers[2], DIGITIZED, blank_cell)
            low.put(id, yes, VOTES, answers[0], DIGITIZED, yes_cell)
            low.put(id, no, VOTES, answers[1], DIGITIZED, no_cell)
            added = (value(reg), value(vot), answers[0] + answers[1], value(vot) - value(val), answers[2], *answers[:2])
            for i, n in enumerate(added):
                totals[i] += n
        if totals != expected:
            raise ValueError(f"the voivodeships add up to {totals}, not to the national totals {expected}")
        p.frame(
            "declared",
            pl.DataFrame(
                [
                    (low.result_set, None, REGISTERED, expected[0]),
                    (low.result_set, None, CAST, expected[1]),
                    (low.result_set, None, VALID, expected[2]),
                    (low.result_set, None, INVALID, expected[3]),
                    (low.result_set, None, BLANK, expected[4]),
                    (low.result_set, yes, VOTES, expected[5]),
                    (low.result_set, no, VOTES, expected[6]),
                ],
                schema={"result_set_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
                orient="row",
            ),
        )
        for out in sets.values():
            stage_values(p, out)
    # Page images for every page that carries a box.
    cited = sorted(
        {c[1] for c in (registered, voters, valid_cards, invalid)}
        | {c[1] for q in nat_q for c in q}
        | {c[1] for u in units for c in (u["registered"], u["voters"], u["valid"])}
        | {c[1] for u in units for q in u["q"] for c in q if c[1] is not None}
    )
    image = {int(path.stem.split("-")[1]): path for path in pages()}
    rows = []
    for page in cited:
        width, height = sizes[str(page)]
        rows.append(
            {
                "id": p.id("page", page),
                "artifact_id": artifact_id,
                "page_number": page,
                "image_key": f"pages/{SOURCE}/{image[page].name}",
                "width": width,
                "height": height,
            }
        )
    p.frame("source_page", pl.DataFrame(rows, schema_overrides={"page_number": pl.Int32}))
    return p


def stage_values(p: Package, out: Values) -> None:
    p.frame(
        "result_value",
        pl.DataFrame(
            out.values,
            schema={
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "flags": pl.Int16,
            },
            orient="row",
        ).with_columns(
            result_set_id=pl.lit(out.result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            [(u, o, m, p.id("page", page), *box) for u, o, m, page, box in out.locators],
            schema={
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "page_id": pl.String,
                "x0": pl.Float32,
                "y0": pl.Float32,
                "x1": pl.Float32,
                "y1": pl.Float32,
            },
            orient="row",
        ).with_columns(
            result_set_id=pl.lit(out.result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
        ),
    )


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())
