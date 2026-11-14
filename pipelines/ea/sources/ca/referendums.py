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
VERSION = "4"
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
ELECTIONS_CANADA_TERMS = "Elections Canada website terms: non-commercial reproduction only, commercial redistribution needs written permission"
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
PROCLAMATION = "https://www.canadiana.ca/view/oocihm.59965"
PROCLAMATION_TITLE = (
    "Proclamation: prohibition plebiscite, Electoral District of Durham, West Riding, Province of Ontario, Dominion "
    "of Canada (returning officer, Bowmanville, 1898)"
)
PLEBISCITE_QUESTION = (
    "Are you in favor of the passing of an Act prohibiting the Importation, Manufacture or Sale of Spirits, Wine, "
    "Ale, Beer, Cider and all other Alcoholic Liquors for use as Beverages?"
)
PLEBISCITE_DISTRICTS = Path(__file__).with_name("referendum_1898_districts.tsv")
RECAP_PAGE = 354
RECAP = {
    "35": (154499, 115275, 269774, 576784),
    "24": (28582, 122614, 151196, 335678),
    "12": (34646, 5402, 40048, 101492),
    "13": (26911, 9576, 36487, 90003),
    "11": (9461, 1146, 10607, 23388),
    "46": (12419, 2978, 15397, 49262),
    "59": (5731, 4756, 10487, 35537),
    "CA": (278487, 264571, 543058, 1236419),
}
RECAP_TERRITORIES = ((1708, 1331, 3039, 9522), (3919, 1166, 5085, 11876), (611, 327, 938, 2877))
RECAP_DRIFT = {"24": (2, 10)}
RECAP_FIELDS = ("for", "against", "valid", "registered")
RECAP_COLUMNS = ((1610, 1760), (1935, 2085), (2915, 3065), (3215, 3395))
RECAP_ROWS = {"35": 960, "24": 999, "12": 1039, "13": 1079, "11": 1119, "46": 1158, "59": 1197, "CA": 1395}
ERRATA = {
    "Bothwell",
    "Huron West",
    "London",
    "Ontario North",
    "Compton",
    "Dorchester",
    "Wright",
    "Kings",
    "City and County of St. John",
    "City of St. John",
    "Burrard",
}
PLEBISCITE_SYSTEM = (
    "A consultative plebiscite under the Prohibition Plebiscite Act (61 Victoria, chapter 51). The question was put "
    "to the electors on the federal voters' lists in every province and in the North-West Territories, and the "
    "returns were made to the Clerk of the Crown in Chancery. The Act set no threshold and bound Parliament to "
    "nothing."
)
PLEBISCITE_METHOD = (
    "Votes by province as the Clerk of the Crown in Chancery printed them in the recapitulation of his report on "
    "the plebiscite (Sessional Paper No. 20, 1899), with the total of votes for and against stored as valid votes, "
    "and the voters on the lists. The figures were read from the scanned page and checked: each row's votes for "
    "and against add to its total, and the rows add to the printed national total. Nova Scotia's votes for are "
    "34,646; the scan's second digit is worn, and 34,646 is what Nova Scotia's total, its majority, and the national "
    "total give. The recapitulation prints the North-West Territories as the provisional districts of Alberta, "
    "Assiniboia, and Saskatchewan, so they have no row here; their electoral districts are in the district result "
    "set. Its Quebec votes are 2 higher for and 10 higher against than the Quebec districts add to as printed, and "
    "its electors differ from the sums of the districts' printed figures in Ontario, Quebec, Nova Scotia, and "
    "Manitoba, several of which the report corrects in footnotes. The report adds that after deducting 1,630 votes "
    "counted through clerical errors and the double vote in St. John, the majority for prohibition would be 12,286."
)
PLEBISCITE_DISTRICT_METHOD = (
    "Votes in each of the 206 electoral districts as the Clerk of the Crown in Chancery printed them on the totals "
    "line that closes each district in his report on the plebiscite, read from the scanned volume and checked by "
    "hand against the page: votes for and against, the total of votes polled (stored as valid votes), and the voters "
    "on the revised lists. Rejected and spoiled ballots and census population are printed too and not stored. The "
    "districts are those of the 1892 Representation Act used at the 1896 general election. The for and against "
    "figures add to the recapitulation's provincial totals everywhere but Quebec, where they are 2 and 10 lower. "
    "Eleven totals do not add up as printed and are stored as printed: Bothwell, Huron West, London, Ontario North, "
    "Compton, Dorchester, Wright, Kings (Nova Scotia), City and County of St. John, City of St. John, and Burrard. "
    "Waterloo North prints no total, and four Prince Edward Island districts print no voters' list figure."
)
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
    raw.fetch(SOURCE, PROCLAMATION, suffix=".html")
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
    plebiscite = pl.read_csv(
        PLEBISCITE_DISTRICTS, separator="\t", schema_overrides={"province": pl.String, "page": pl.Int64}
    ).rename({"polled": "valid", "electors": "registered"})
    if plebiscite["district"].n_unique() != plebiscite.height:
        raise ValueError("a 1898 district appears twice")
    for row in plebiscite.iter_rows(named=True):
        if (row["valid"] is not None and row["for"] + row["against"] != row["valid"]) != (row["name"] in ERRATA):
            raise ValueError(f"{row['name']}: the 1898 row's figures do not match the known errata")
    for code, printed in [*RECAP.items(), *(("61", total) for total in RECAP_TERRITORIES)]:
        if printed[0] + printed[1] != printed[2]:
            raise ValueError(f"{code}: the 1898 recapitulation row does not add up")
    for column in range(4):
        provinces_total = sum(row[column] for code, row in RECAP.items() if code != "CA")
        if provinces_total + sum(row[column] for row in RECAP_TERRITORIES) != RECAP["CA"][column]:
            raise ValueError(f"{RECAP_FIELDS[column]}: the 1898 provinces do not add to the printed total")
    sums = plebiscite.group_by("province").agg(pl.col("for").sum(), pl.col("against").sum())
    for row in sums.iter_rows(named=True):
        printed = (
            [sum(total[column] for total in RECAP_TERRITORIES) for column in range(2)]
            if row["province"] == "61"
            else RECAP[row["province"]]
        )
        drift = RECAP_DRIFT.get(row["province"], (0, 0))
        if (printed[0] - row["for"], printed[1] - row["against"]) != drift:
            raise ValueError(f"{row['province']}: the 1898 districts do not add to the recapitulation")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    districts.write_parquet(PARSED / "districts.parquet")
    plebiscite.write_parquet(PARSED / "plebiscite.parquet")
    print(frame)
    print(districts)
    print(plebiscite)


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


def upright(seq: int) -> tuple[Path, int, int, int, int]:
    PAGES.mkdir(parents=True, exist_ok=True)
    scan = raw.cached(SOURCE, page_images()[seq - 1])
    image = rotated(seq)
    width, height = image.size
    path = PAGES / f"{scan.sha256}-upright.png"
    if not path.exists():
        image.thumbnail((2400, 2400))
        image.save(path, "PNG", optimize=True)
    small_width, small_height = struct.unpack(">II", path.read_bytes()[16:24])
    return path, small_width, small_height, width, height


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
                "Canada's federal referendum and plebiscite results: the 1992 referendum on the Charlottetown Accord, "
                "with the Chief Electoral Officer's totals by province and territory and by electoral district, and "
                "the 1898 prohibition plebiscite, with the Clerk of the Crown in Chancery's totals by province and by "
                "electoral district"
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
            license=ELECTIONS_CANADA_TERMS,
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
            license=ELECTIONS_CANADA_TERMS,
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

        def province(code: str) -> UUID:
            if code in units:
                return units[code]
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
            return unit

        for code in results["code"]:
            province(code)

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
        plebiscite = pl.read_parquet(PARSED / "plebiscite.parquet")
        chancery = cs.source("Clerk of the Crown in Chancery", VOLUME_TITLE)
        cs.add(
            "source",
            id=chancery,
            kind="official_body",
            publisher="Clerk of the Crown in Chancery",
            title=VOLUME_TITLE,
            url=VOLUME,
            license="Public domain (Crown copyright expired); scanned and served by Canadiana",
        )
        proclamation = cs.source("Returning officer, Durham West", PROCLAMATION_TITLE)
        cs.add(
            "source",
            id=proclamation,
            kind="official_body",
            publisher="Returning officer, Durham West",
            title=PROCLAMATION_TITLE,
            url=PROCLAMATION,
            license="Public domain; scanned from Toronto Public Library microfiche and served by Canadiana",
        )
        volume = cs.artifact(raw.cached(SOURCE, VOLUME), chancery)
        cs.artifact(raw.cached(SOURCE, PROCLAMATION), proclamation)
        sheets = {}
        for seq in sorted({*plebiscite["page"], RECAP_PAGE}):
            scan, scan_width, scan_height, width, height = upright(seq)
            sheets[seq] = (cs.page(volume, seq), width, height)
            cs.add(
                "source_page",
                id=sheets[seq][0],
                artifact_id=volume,
                page_number=seq,
                image_key=f"pages/{SOURCE}/{scan.name}",
                width=scan_width,
                height=scan_height,
            )
        plebiscite_system = cs.key("ca.referendums.electoral_system", "plebiscite-1898")
        cs.add(
            "electoral_system",
            id=plebiscite_system,
            polity_id=canada,
            rules={"formula": "referendum"},
            rules_version=1,
        )
        name(plebiscite_system, "Canadian federal prohibition plebiscite of 1898")
        cs.add("note", entity_id=plebiscite_system, kind="methodology", body_md=PLEBISCITE_SYSTEM)
        plebiscite_election = cs.key("ca.referendums.election", str(PLEBISCITE))
        cs.add(
            "election",
            id=plebiscite_election,
            polity_id=canada,
            kind="referendum",
            date_from=PLEBISCITE,
            date_to=PLEBISCITE,
            status="held",
        )
        name(plebiscite_election, "1898 Canadian prohibition plebiscite")
        name(plebiscite_election, "Plébiscite canadien de 1898 sur la prohibition", "fr", "native")
        plebiscite_path = f"ca/{PLEBISCITE}-plebiscite"
        cs.add("slug", path=plebiscite_path, entity_id=plebiscite_election)
        plebiscite_contest = cs.key("ca.referendums.contest", str(PLEBISCITE))
        cs.add(
            "contest",
            id=plebiscite_contest,
            election_id=plebiscite_election,
            kind="ballot_question",
            electoral_system_id=plebiscite_system,
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=plebiscite_contest, type="plebiscite")
        name(plebiscite_contest, PLEBISCITE_QUESTION)
        cs.add("slug", path=f"{plebiscite_path}/question", entity_id=plebiscite_contest)
        sides = {}
        for position, (side, label) in enumerate((("for", "For prohibition"), ("against", "Against prohibition")), 1):
            sides[side] = cs.key("ca.referendums.option", f"{PLEBISCITE}:{side}")
            cs.add(
                "contest_option",
                id=sides[side],
                contest_id=plebiscite_contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        winner = "for" if RECAP["CA"][0] > RECAP["CA"][1] else "against"
        recap_set = cs.key("ca.referendums.result_set", str(PLEBISCITE))
        plebiscite_district_set = cs.key("ca.referendums.result_set", f"{PLEBISCITE}:district")
        for result, default, body in (
            (recap_set, True, PLEBISCITE_METHOD),
            (plebiscite_district_set, False, PLEBISCITE_DISTRICT_METHOD),
        ):
            cs.add(
                "result_set",
                id=result,
                election_id=plebiscite_election,
                contest_id=plebiscite_contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=chancery,
                is_default=default,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=body)
            for side, option in sides.items():
                cs.add("outcome", result_set_id=result, option_id=option, kind="won" if side == winner else "lost")
        measures = {"for": VOTES, "against": VOTES, "valid": VALID, "registered": REGISTERED}
        recap_page, recap_width, recap_height = sheets[RECAP_PAGE]
        for code, printed in RECAP.items():
            for field, value, (left, right) in zip(RECAP_FIELDS, printed, RECAP_COLUMNS, strict=True):
                option = sides.get(field)
                key = (str(recap_set), str(province(code)), None if option is None else str(option), measures[field])
                values.append((*key, value))
                row = RECAP_ROWS[code]
                locators.append(
                    (
                        *key,
                        str(recap_page),
                        left / recap_width,
                        (row - 8) / recap_height,
                        right / recap_width,
                        (row + 44) / recap_height,
                    )
                )
        for row in plebiscite.iter_rows(named=True):
            unit = cs.unit("ca.fed", row["district"], PLEBISCITE)
            if unit in cs.minted:
                raise ValueError(f"{row['name']}: no 1896 riding unit for {row['district']}")
            sheet, width, height = sheets[row["page"]]
            for field, column in zip(RECAP_FIELDS, ("for", "against", "polled", "electors"), strict=True):
                if row[field] is None:
                    continue
                option = sides.get(field)
                key = (
                    str(plebiscite_district_set),
                    str(unit),
                    None if option is None else str(option),
                    measures[field],
                )
                values.append((*key, row[field]))
                x0, y0, x1, y1 = map(int, row[f"{column}_box"].split())
                locators.append((*key, str(sheet), x0 / width, y0 / height, x1 / width, y1 / height))
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
