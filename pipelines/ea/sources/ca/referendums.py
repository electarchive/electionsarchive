import base64
import io
import json
import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset
from ea.sources.ca.elections import PROVINCES
from ea.store import DATA, connect

SOURCE = "ca.referendums"
VERSION = "3"
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
SYNOPSIS = "https://archive.org/download/31761115535080/31761115535080.pdf"
SYNOPSIS_PAGE = "https://archive.org/details/31761115535080"
SYNOPSIS_TITLE = "Referendum 92: Official Voting Results, Synopsis (Chief Electoral Officer of Canada)"
DISTRICTS = Path(__file__).with_name("referendum_1992_districts.tsv")
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
SYNOPSIS_DPI = 400
SYNOPSIS_COLUMNS = {
    19: (1316, 1630, 1781, 1940, 2078, 2256),
    20: (1063, 1380, 1533, 1692, 1831, 2010),
    21: (1304, 1618, 1770, 1929, 2067, 2245),
    22: (1024, 1340, 1493, 1652, 1792, 1971),
    23: (1318, 1632, 1784, 1941, 2080, 2257),
    24: (1038, 1354, 1507, 1666, 1805, 1985),
    25: (1328, 1643, 1794, 1952, 2090, 2267),
}
SYNOPSIS_FIELDS = ("registered", "yes", "no", "valid", "invalid", "ballots")
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
    "the federal count. The results by electoral district are a separate result set from the Chief Electoral "
    "Officer's synopsis of the official voting results."
)
DISTRICT_METHOD = (
    "Votes in each of the 220 electoral districts outside Quebec as the Chief Electoral Officer of Canada printed "
    "them in Table 3 of Referendum 92: Official Voting Results, Synopsis, read from the University of Toronto's scan "
    "and checked: each district's Yes and No add to its valid votes, valid votes and rejected ballots add to ballots "
    "cast, and the districts add to every printed provincial total, which match the Chief Electoral Officer's report. "
    "The districts are those of the 1987 Representation Order used at the 1988 general election. Ten were renamed in "
    "1990, and the synopsis prints the new names: Acadie-Bathurst (formerly Gloucester), Fredericton-York-Sunbury "
    "(Fredericton), Restigouche-Chaleur (Restigouche), Bramalea-Gore-Malton (Brampton-Malton), Elgin-Norfolk "
    "(Elgin), Markham-Whitchurch-Stouffville (Markham), Renfrew-Nipissing-Pembroke (Renfrew), Windsor-St. Clair "
    "(Windsor-Lake St. Clair), Selkirk-Red River (Selkirk), and Surrey-White Rock-South Langley (Surrey-White Rock)."
)


PLEBISCITE = date(1898, 9, 29)
VOLUME = "https://www.canadiana.ca/view/oocihm.9_08052_33_14"
VOLUME_TITLE = (
    "Report on the Prohibition Plebiscite held on the 29th day of September, 1898 (Clerk of the Crown in Chancery), "
    "Sessional Papers of the Dominion of Canada, 1899, volume 14, Sessional Paper No. 20"
)
PLEBISCITE_PAGES = range(45, 355)
MODEL = "claude-haiku-5-5"
PROMPT = "1"
LONGEST = 1568
MEASURES = ("for", "against", "polled", "rejected", "spoiled", "electors", "population")
READ_SYSTEM = (
    "You read one page of the 1898 Canadian prohibition plebiscite report. The page is a table of polling divisions "
    "grouped by electoral district. The image joins two strips: on the left, the column that names the electoral "
    "district (and the margin note such as 'ADDINGTON (Continued-Suite)'); on the right, the number columns in this "
    "order: votes for prohibition, votes against prohibition, total number of votes polled, rejected ballots, spoiled "
    "ballots, number of voters on the revised voters' list, population of the constituency by the last census, and "
    "remarks. A district ends with a line 'Totals-Totaux' set between rules after its last polling division (the label "
    "may be cut off; the row has larger figures) and then a 'Majority for/against prohibition' line. Report every "
    "Totals line on the page with the electoral district it closes, copying the figures exactly as printed (dots or "
    "blanks are null). Copy any remark printed on the Totals line or flagged with an asterisk or dagger on it, such "
    "as 'Should be 3,975'. Also list every electoral district named on the page."
)
READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "districts": {"type": "array", "items": {"type": "string"}},
        "totals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "district": {"type": "string"},
                    **{measure: {"type": ["integer", "null"]} for measure in MEASURES},
                    "majority": {"type": ["integer", "null"]},
                    "majority_side": {"type": "string", "enum": ["for", "against", "none"]},
                    "remarks": {"type": "string"},
                },
                "required": ["district", *MEASURES, "majority", "majority_side", "remarks"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["districts", "totals"],
    "additionalProperties": False,
}


def page_images() -> list[str]:
    html = raw.cached(SOURCE, VOLUME).path.read_text()
    return [f"{uri}/full/max/0/default.jpg" for uri in re.findall(r'data-uri="([^"]+)/info.json"', html)]


def rotated(seq: int) -> Image.Image:
    return Image.open(raw.cached(SOURCE, page_images()[seq - 1]).path).convert("L").rotate(-90, expand=True)


def strips(image: Image.Image) -> Image.Image:
    width, height = image.size
    label = image.crop((int(width * 0.02), 0, int(width * 0.16), height))
    body = image.crop((int(width * 0.37), 0, int(width * 0.88), height))
    joined = Image.new("L", (label.width + 8 + body.width, height), 255)
    joined.paste(label, (0, 0))
    joined.paste(Image.new("L", (4, height), 0), (label.width + 2, 0))
    joined.paste(body, (label.width + 8, 0))
    joined.thumbnail((LONGEST, LONGEST))
    return joined


def read_key(seq: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/1898/{seq}"


def read(seq: int) -> dict:
    message = json.loads(raw.cached(SOURCE, read_key(seq)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def fetch() -> None:
    raw.fetch(SOURCE, REPORT, suffix=".pdf")
    raw.fetch(SOURCE, WORDING, suffix=".html")
    raw.fetch(SOURCE, VOLUME, suffix=".html")
    raw.fetch(SOURCE, SYNOPSIS, suffix=".pdf")
    images = page_images()
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for seq in PLEBISCITE_PAGES:
        raw.fetch(SOURCE, images[seq - 1], suffix=".jpg")
        tile = strips(rotated(seq))
        buffer = io.BytesIO()
        tile.save(buffer, "PNG")
        params = MessageCreateParamsNonStreaming(
            model=MODEL,
            max_tokens=4000,
            system=READ_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        ImageBlockParam(
                            type="image",
                            source={
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(buffer.getvalue()).decode(),
                            },
                        ),
                        TextBlockParam(type="text", text="Read the Totals lines on this page."),
                    ],
                }
            ],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
        )
        wanted[read_key(seq)] = (params, (tile.width * tile.height / 750 * price_in + 1500 * price_out) / 1e6)
    print(batch.ensure(SOURCE, "plebiscite-1898", wanted)[1])


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
    districts = pl.read_csv(DISTRICTS, separator="\t", schema_overrides={"province": pl.String}).rename(
        {"electors": "registered", "rejected": "invalid"}
    )
    if districts["district"].n_unique() != districts.height:
        raise ValueError("a district appears twice")
    for row in districts.iter_rows(named=True):
        if row["yes"] + row["no"] != row["valid"] or row["valid"] + row["invalid"] != row["ballots"]:
            raise ValueError(f"{row['name']}: the row's figures do not add up")
    sums = districts.group_by("province").agg(pl.col(column).sum() for column in SYNOPSIS_FIELDS)
    for row in sums.iter_rows(named=True):
        printed = provinces.filter(pl.col("code") == row["province"]).row(0, named=True)
        for column in SYNOPSIS_FIELDS:
            if row[column] != printed[column]:
                raise ValueError(f"{row['province']} {column}: the districts do not add to the provincial total")
    if sums.height != provinces.height:
        raise ValueError("a province has no districts")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    districts.write_parquet(PARSED / "districts.parquet")
    print(frame)
    print(districts)


def render(path: Path, sha256: str, number: int, dpi: int) -> tuple[Path, int, int]:
    PAGES.mkdir(parents=True, exist_ok=True)
    image = PAGES / f"{sha256}-{number}.png"
    if not image.exists():
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                str(dpi),
                "-gray",
                "-png",
                "-f",
                str(number),
                "-l",
                str(number),
                "-singlefile",
                str(path),
                str(image.with_suffix("")),
            ],
            check=True,
        )
    width, height = struct.unpack(">II", image.read_bytes()[16:24])
    return image, width, height


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    districts = pl.read_parquet(PARSED / "districts.parquet")
    report = raw.cached(SOURCE, REPORT)
    synopsis = raw.cached(SOURCE, SYNOPSIS)
    image, width, height = render(report.path, report.sha256, TABLE_PAGE, RENDER_DPI)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Canada's 1992 federal referendum on the Charlottetown Accord, with the Chief Electoral Officer's "
                "totals by province and territory and by electoral district"
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
            license="Public record",
        )
        synopsis_source = cs.source("Elections Canada", SYNOPSIS_TITLE)
        cs.add(
            "source",
            id=synopsis_source,
            kind="official_body",
            publisher="Elections Canada",
            title=SYNOPSIS_TITLE,
            url=SYNOPSIS_PAGE,
            license="Crown copyright; scanned by the University of Toronto Libraries and served by the Internet Archive",
        )
        artifact = cs.artifact(report, elections_canada)
        cs.artifact(raw.cached(SOURCE, WORDING), wording)
        synopsis_artifact = cs.artifact(synopsis, synopsis_source)
        synopsis_pages = {}
        scales = {}
        for number in SYNOPSIS_COLUMNS:
            scan, scan_width, scan_height = render(synopsis.path, synopsis.sha256, number, RENDER_DPI)
            synopsis_pages[number] = cs.page(synopsis_artifact, number)
            scales[number] = (scan_width * SYNOPSIS_DPI / RENDER_DPI, scan_height * SYNOPSIS_DPI / RENDER_DPI)
            cs.add(
                "source_page",
                id=synopsis_pages[number],
                artifact_id=synopsis_artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{scan.name}",
                width=scan_width,
                height=scan_height,
            )
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
        district_set = cs.key("ca.referendums.result_set", f"{DAY}:district")
        cs.add(
            "result_set",
            id=district_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=synopsis_source,
            is_default=False,
            completeness=1,
        )
        cs.add("note", entity_id=district_set, kind="methodology", body_md=DISTRICT_METHOD)
        for answer, option in options.items():
            cs.add(
                "outcome",
                result_set_id=district_set,
                option_id=option,
                kind="won" if national[answer] == max(national["yes"], national["no"]) else "lost",
            )
        values = []
        locators = []
        for row in districts.iter_rows(named=True):
            unit = cs.unit("ca.fed", row["district"], DAY)
            if unit in cs.minted:
                raise ValueError(f"{row['name']}: no 1988 riding unit for {row['district']}")
            top = row["top"]
            scan_width, scan_height = scales[row["page"]]
            for column, right in zip(SYNOPSIS_FIELDS, SYNOPSIS_COLUMNS[row["page"]], strict=True):
                option = options.get(column)
                measure = {"registered": REGISTERED, "ballots": BALLOTS, "valid": VALID, "invalid": INVALID}.get(
                    column, VOTES
                )
                key = (str(district_set), str(unit), None if option is None else str(option), measure)
                values.append((*key, row[column]))
                locators.append(
                    (
                        *key,
                        str(synopsis_pages[row["page"]]),
                        (right - 160) / scan_width,
                        (top - 12) / scan_height,
                        (right + 12) / scan_width,
                        (top + 48) / scan_height,
                    )
                )
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
