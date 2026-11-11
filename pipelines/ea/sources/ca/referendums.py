import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.ca.elections import PROVINCES
from ea.store import DATA, connect

SOURCE = "ca.referendums"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
DAY = date(1992, 10, 26)
REPORT = "https://www.elections.ca/res/rep/off/1992/1992_Referendum_Part_2_E.pdf"
REPORT_PAGE = "https://www.elections.ca/content.aspx?section=res&dir=rep/off/1992&document=index&lang=e"
REPORT_TITLE = "The 1992 Federal Referendum: A Challenge Met (Report of the Chief Electoral Officer of Canada), Part 2"
WORDING = (
    "https://epe.lac-bac.gc.ca/100/205/301/pco-bcp/website/06-07-27/www.pco-bcp.gc.ca/aia/printer.asp"
    "@language=e&page=consfile&sub=referendaquestionsandres&doc=1992_e.htm&printerfriendly=y.htm?nodisclaimer=1"
)
WORDING_TITLE = "Question and results of the 1992 referendums (Privy Council Office, archived)"
QUESTION = (
    "Do you agree that the Constitution of Canada should be renewed on the basis of the agreement reached on "
    "August 28, 1992?"
)
TABLE_PAGE = 26
RENDER_DPI = 200
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
COLUMNS = {
    "yes": (440, 600),
    "no": (700, 860),
    "valid": (990, 1130),
    "invalid": (1270, 1400),
    "ballots": (1510, 1660),
    "registered": (1770, 1910),
}
ROWS = {
    "10": 746,
    "11": 779,
    "12": 812,
    "13": 846,
    "35": 880,
    "46": 913,
    "47": 946,
    "48": 979,
    "59": 1012,
    "61": 1046,
    "60": 1079,
    "CA": 1112,
}
TABLE = {
    "10": (133583, 77742, 211325, 1068, 212393, 398269),
    "11": (48541, 17128, 65669, 305, 65974, 93534),
    "12": (218967, 229690, 448657, 2065, 450722, 664594),
    "13": (234469, 144885, 379354, 2642, 381996, 529071),
    "35": (2409713, 2395465, 4805178, 29564, 4834742, 6719895),
    "46": (199905, 320918, 520823, 2370, 523193, 740627),
    "47": (203525, 251441, 454966, 1644, 456610, 664917),
    "48": (484472, 732457, 1216929, 2958, 1219887, 1679700),
    "59": (528773, 1139127, 1667900, 6047, 1673947, 2183398),
    "61": (14723, 9280, 24003, 169, 24172, 34333),
    "60": (5360, 6916, 12276, 66, 12342, 17628),
    "CA": (4482031, 5325049, 9807080, 48898, 9855978, 13725966),
}
SYSTEM = (
    "A consultative referendum under the Referendum Act of June 23, 1992, on the Constitution of Canada. The "
    "Governor in Council proclaimed the question, and the Chief Electoral Officer ran the vote on the federal "
    "lists. The Act sets no threshold; the question was put to every elector in the nine provinces and the two "
    "territories that held it, and the outcome is the majority of valid votes across them."
)
METHOD = (
    "Votes by province and territory as the Chief Electoral Officer of Canada printed them in Table 9 of his report "
    "on the 1992 referendum, with electors on the lists, ballots cast, valid votes, and rejected ballots. The "
    "figures were read from the scanned table and checked: each row's Yes and No add to its valid votes, valid "
    "votes and rejected ballots add to ballots cast, and the provinces add to the printed total. Quebec is absent: "
    "it held its own referendum on the same day and question under its own law, and its results are not part of "
    "the federal count. The report prints no result by electoral district; those are in the separate book "
    "Referendum 92: Official Voting Results."
)


def fetch() -> None:
    raw.fetch(SOURCE, REPORT, suffix=".pdf")
    raw.fetch(SOURCE, WORDING, suffix=".html")


def box(code: str, column: str) -> tuple[float, float, float, float]:
    width, height = 1700, 2200
    left, right = COLUMNS[column]
    centre = ROWS[code]
    return (
        (centre - 14) / width,
        (height - right) / height,
        (centre + 14) / width,
        (height - left) / height,
    )


def parse() -> None:
    rows = []
    for code, (yes, no, valid, invalid, ballots, registered) in TABLE.items():
        if yes + no != valid or valid + invalid != ballots:
            raise ValueError(f"{code}: the row's figures do not add up")
        rows.append((code, yes, no, valid, invalid, ballots, registered))
    frame = pl.DataFrame(rows, schema=["code", "yes", "no", "valid", "invalid", "ballots", "registered"], orient="row")
    provinces = frame.filter(pl.col("code") != "CA")
    total = frame.filter(pl.col("code") == "CA").row(0, named=True)
    for column in ("yes", "no", "valid", "invalid", "ballots", "registered"):
        if provinces[column].sum() != total[column]:
            raise ValueError(f"{column}: the provinces do not add to the printed total")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    print(frame)


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    report = raw.cached(SOURCE, REPORT)
    PAGES.mkdir(parents=True, exist_ok=True)
    image = PAGES / f"{report.sha256}-{TABLE_PAGE}.png"
    if not image.exists():
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                str(RENDER_DPI),
                "-gray",
                "-png",
                "-f",
                str(TABLE_PAGE),
                "-l",
                str(TABLE_PAGE),
                "-singlefile",
                str(report.path),
                str(image.with_suffix("")),
            ],
            check=True,
        )
    width, height = struct.unpack(">II", image.read_bytes()[16:24])
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Canada's 1992 federal referendum on the Charlottetown Accord, with the Chief Electoral Officer's "
                "totals by province and territory"
            ),
            polity="CA",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        canada = cs.polity("CA")
        elections_canada = cs.source("Elections Canada", REPORT_TITLE)
        cs.add(
            "source",
            id=elections_canada,
            kind="official_body",
            publisher="Elections Canada",
            title=REPORT_TITLE,
            url=REPORT_PAGE,
            license="Elections Canada website terms: non-commercial reproduction only, commercial redistribution needs written permission",
        )
        wording = cs.source("Privy Council Office", WORDING_TITLE)
        cs.add(
            "source",
            id=wording,
            kind="official_body",
            publisher="Privy Council Office",
            title=WORDING_TITLE,
            url=WORDING,
        )
        artifact = cs.artifact(report, elections_canada)
        cs.artifact(raw.cached(SOURCE, WORDING), wording)
        page = cs.page(artifact, TABLE_PAGE)
        cs.add(
            "source_page",
            id=page,
            artifact_id=artifact,
            page_number=TABLE_PAGE,
            image_key=f"pages/{SOURCE}/{image.name}",
            width=width,
            height=height,
        )

        system = cs.key("ca.referendums.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=canada, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Canadian federal referendum")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        nation = cs.area("ca.nation", "CA")
        units: dict[str, UUID] = {}
        for code in results["code"]:
            scheme, unit_code = ("ca.nation", "CA") if code == "CA" else ("ca.province", code)
            area = nation if code == "CA" else cs.area(scheme, code)
            if area in cs.minted:
                raise ValueError(f"{scheme} {unit_code}: no area to attach the unit to")
            unit = cs.unit(scheme, unit_code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=canada,
                    scheme_code=scheme,
                    code=unit_code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, "Canada" if code == "CA" else PROVINCES[code][1])
            units[code] = unit

        election = cs.key("ca.referendums.election", str(DAY))
        cs.add("election", id=election, polity_id=canada, kind="referendum", date_from=DAY, date_to=DAY, status="held")
        name(election, "1992 Canadian federal referendum")
        name(election, "Référendum fédéral canadien de 1992", "fr", "native")
        path = f"ca/{DAY}-referendum"
        cs.add("slug", path=path, entity_id=election)
        contest = cs.key("ca.referendums.contest", str(DAY))
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            electoral_system_id=system,
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=contest, type="plebiscite")
        name(contest, QUESTION)
        cs.add("slug", path=f"{path}/question", entity_id=contest)
        options = {}
        for position, (answer, label) in enumerate((("yes", "Yes"), ("no", "No")), 1):
            options[answer] = cs.key("ca.referendums.option", f"{DAY}:{answer}")
            cs.add(
                "contest_option",
                id=options[answer],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        national = results.filter(pl.col("code") == "CA").row(0, named=True)
        result_set = cs.key("ca.referendums.result_set", str(DAY))
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=elections_canada,
            is_default=True,
            completeness=1,
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
        for answer, option in options.items():
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if national[answer] == max(national["yes"], national["no"]) else "lost",
            )
        values = []
        locators = []
        for row in results.iter_rows(named=True):
            for column, option, measure in (
                ("yes", options["yes"], VOTES),
                ("no", options["no"], VOTES),
                ("registered", None, REGISTERED),
                ("ballots", None, BALLOTS),
                ("valid", None, VALID),
                ("invalid", None, INVALID),
            ):
                key = (str(result_set), str(units[row["code"]]), None if option is None else str(option), measure)
                values.append((*key, row[column]))
                x0, y0, x1, y1 = box(row["code"], column)
                locators.append((*key, str(page), x0, y0, x1, y1))
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(DIGITIZED, pl.Int16)
            ),
        )
        cs.frame(
            "value_locator",
            pl.DataFrame(
                locators,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "page_id": pl.String,
                    "x0": pl.Float64,
                    "y0": pl.Float64,
                    "x1": pl.Float64,
                    "y1": pl.Float64,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
        )
        return cs.write()
