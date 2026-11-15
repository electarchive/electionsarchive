import base64
import difflib
import io
import json
import re
import subprocess
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from uuid import UUID

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, Connection, connect

SOURCE = "us.ms_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Official election results"
FILES = {
    date(2023, 11, 7): "https://sos.ms.gov/elections/electionresults/2023General/State%20Certified%20Results/"
    "2023%20Statewide%20Recapitulation%20Sheet.pdf",
}
REPORTS = {
    date(2015, 11, 3): "https://sos.ms.gov/elections/electionresults/2015General/certified%20results/"
    "2015%20Electoral%20Vote%20Report.pdf",
    date(2019, 11, 5): "https://sos.ms.gov/elections/electionresults/2019General/Certified%20Results/"
    "Electoral%20Vote%20Report%20(Amended).pdf",
}
SCANS = {
    date(2007, 11, 6): "https://web.archive.org/web/20080306045210id_/http://www.sos.state.ms.us/elections/2007/"
    "Electoral%20Results/Electoral%20Vote%20Report.pdf",
}
COUNTY_SCANS = {
    date(2007, 11, 6): [
        "Adams",
        "Alcorn",
        "Amite",
        "Attala",
        "Benton",
        "Bolivar",
        "Calhoun",
        "Carroll",
        "Chickasaw",
        "Choctaw",
        "Claiborne",
        "Clarke",
        "Clay",
        "Coahoma",
        "Copiah",
        "Covington",
        "Desoto",
        "Forrest",
        "Franklin",
        "George",
        "Greene",
        "Grenada",
        "Hancock",
        "Harrison",
        "Hinds",
        "Holmes",
        "Humphreys",
        "Issaquena",
        "Itawamba",
        "Jackson",
        "Jasper",
        "Jefferson Davis",
        "Jefferson",
        "Jones",
        "Kemper",
        "Lafayette",
        "Lamar",
        "Lauderdale",
        "Lawrence",
        "Leake",
        "Lee",
        "Leflore",
        "Lincoln",
        "Lowndes",
        "Madison",
        "Marion",
        "Marshall",
        "Monroe",
        "Montgomery",
        "Neshoba",
        "Newton",
        "Noxubee",
        "Oktibbeha",
        "Panola",
        "Pearl River",
        "Perry",
        "Pike",
        "Pontotoc",
        "Prentiss",
        "Quitman",
        "Rankin",
        "Scott",
        "Sharkey",
        "Simpson",
        "Smith",
        "Stone",
        "Sunflower",
        "Tallahatchie",
        "Tate",
        "Tippah",
        "Tishomingo",
        "Tunica",
        "Union",
        "Walthall",
        "Warren",
        "Washington",
        "Wayne",
        "Webster",
        "Wilkinson",
        "Winston",
        "Yalobusha",
        "Yazoo",
    ],
}
COUNTY_SCAN = (
    "https://web.archive.org/web/20080306044000id_/http://www.sos.state.ms.us/elections/{year}/General%20Election/General/"
    "{county}.pdf"
)
PAGES = DATA / "pages" / SOURCE
REPORT_TITLE = "Electoral Vote Assignment"
COUNTY_TITLE = "Official county returns"
DISTRICTS = "us.ms_house_district"
OFFICES = {
    "Governor": "Governor",
    "Lieutenant Governor": "Lieutenant Governor",
    "Secretary Of State": "Secretary of State",
    "Attorney General": "Attorney General",
    "State Auditor": "Auditor",
    "State Treasurer": "Treasurer",
    "Commissioner Of Agriculture & Commerce": "Commissioner of Agriculture and Commerce",
    "Commissioner Of Insurance": "Commissioner of Insurance",
}
PARTIES = {
    "Democrat": "democratic-party",
    "Republican": "republican-party",
    "Libertarian": "libertarian-party",
    "Constitution": "constitution-party",
}
SAMPLE_BALLOT = (
    "https://web.archive.org/web/20080305194659id_/http://www.sos.state.ms.us/elections/2007/Forms/"
    "07%20GE%20Sample%20Ballot%20_4_.pdf"
)
BALLOT = {
    "Governor": [("Haley Barbour", "Republican"), ("John Arthur Eaves, Jr.", "Democrat")],
    "Lieutenant Governor": [("Phil Bryant", "Republican"), ("James R. 'Jamie' Franks, Jr.", "Democrat")],
    "Secretary of State": [("Delbert Hosemann", "Republican"), ("Robert H. 'Rob' Smith", "Democrat")],
    "Attorney General": [("Jim Hood", "Democrat"), ("Al Hopkins", "Republican")],
    "Auditor": [("Stacey E. Pickering", "Republican"), ("Mike Sumrall", "Democrat")],
    "Treasurer": [("Shawn O'Hara", "Democrat"), ("Tate Reeves", "Republican")],
    "Commissioner of Agriculture and Commerce": [
        ("Rickey L. Cole", "Democrat"),
        ("Lester Spell", "Republican"),
        ("Paul Leslie Riley", "Constitution"),
    ],
    "Commissioner of Insurance": [("Gary Anderson", "Democrat"), ("Mike Chaney", "Republican")],
}

MODEL = "claude-haiku-5-5"
PROMPT = "1"
LONGEST = 1568
READ_SYSTEM = (
    "You read one scanned page of a Mississippi county's official returns of the general election of November 6, "
    "2007, as filed with the Secretary of State. Layouts vary by county: offices may be column groups with precincts "
    "as rows, or rows with precincts as columns, and one office's precincts may run over several pages. The statewide "
    "offices are Governor, Lieutenant Governor, Secretary of State, Attorney General, State Auditor, State Treasurer, "
    "Commissioner of Agriculture and Commerce, and Commissioner of Insurance. For each of these offices whose "
    "county-wide grand total is printed on this page, copy that total for each named candidate exactly as printed. "
    "The county-wide grand total covers every precinct in the county: the last Total line (or Total column) after "
    "the final precinct, sometimes labeled 'Total', 'Grand Total', 'County Total', or 'Jurisdiction Wide'. Do not "
    "report a subtotal of one precinct, of a group of precincts (such as 'Separated Precincts' or 'Non-Separated "
    "Precincts'), or of one voting method (polling, absentee, affidavit). Leave out write-ins, total votes, "
    "over and under votes, and turnout. Give the candidate as printed (for example 'Barbour, Haley (Rep)'). Copy the "
    "label of the total line or column. If no statewide office's county-wide grand total is on this page, return an "
    "empty list."
)
READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "totals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "office": {"type": "string", "enum": list(BALLOT)},
                    "label": {"type": "string"},
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"candidate": {"type": "string"}, "votes": {"type": "integer"}},
                            "required": ["candidate", "votes"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["office", "label", "candidates"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["totals"],
    "additionalProperties": False,
}


CORRECTED = {
    (date(2007, 11, 6), "Lieutenant Governor", 75, "franks"): 2226,
    (date(2007, 11, 6), "Auditor", 7, "sumrall"): 767,
}

COUNTY_CORRECTED = {
    (date(2007, 11, 6), "Alcorn", 3): {
        "eaves": 3695,
        "sumrall": 4306,
        "bryant": 4913,
        "franks": 3981,
        "barbour": 5363,
        "smith": 3947,
        "pickering": 4183,
        "ohara": 3788,
        "reeves": 4617,
        "cole": 3979,
        "spell": 4048,
        "hood": 4841,
        "hosemann": 4659,
        "hopkins": 3901,
    },
    (date(2007, 11, 6), "Alcorn", 5): {"riley": 598},
    (date(2007, 11, 6), "Benton", 3): {"hopkins": 848, "smith": 1348, "hood": 1384, "hosemann": 813},
    (date(2007, 11, 6), "Calhoun", 3): {"riley": 352},
    (date(2007, 11, 6), "Claiborne", 4): {"chaney": 705},
    (date(2007, 11, 6), "Desoto", 9): {"riley": 1928},
    (date(2007, 11, 6), "Greene", 5): {"riley": 191},
    (date(2007, 11, 6), "Harrison", 8): {"sumrall": 11329},
    (date(2007, 11, 6), "Harrison", 15): {"pickering": 21404},
    (date(2007, 11, 6), "Hinds", 23): {"riley": 3525},
    (date(2007, 11, 6), "Humphreys", 5): {"riley": 302},
    (date(2007, 11, 6), "Issaquena", 2): {"barbour": 364, "franks": 408, "bryant": 347, "eaves": 432},
    (date(2007, 11, 6), "Jasper", 3): {
        "hosemann": 2164,
        "cole": 3160,
        "eaves": 3225,
        "barbour": 2164,
        "franks": 2971,
        "bryant": 2417,
        "smith": 3188,
        "hood": 3970,
        "spell": 2048,
        "hopkins": 1405,
        "sumrall": 3131,
        "pickering": 2232,
        "ohara": 2961,
        "reeves": 2341,
    },
    (date(2007, 11, 6), "Jasper", 5): {"anderson": 3115, "riley": 167, "chaney": 2259},
    (date(2007, 11, 6), "Jefferson Davis", 7): {"riley": 245},
    (date(2007, 11, 6), "Jones", 11): {"riley": 846, "anderson": 6976, "chaney": 12657},
    (date(2007, 11, 6), "Lauderdale", 7): {"smith": 5891, "hosemann": 11976, "hopkins": 7794, "hood": 10240},
    (date(2007, 11, 6), "Lee", 5): {
        "bryant": 10830,
        "eaves": 8389,
        "barbour": 12191,
        "franks": 9880,
        "smith": 6840,
        "hosemann": 13371,
        "sumrall": 7375,
        "pickering": 12595,
        "ohara": 6446,
        "reeves": 13286,
        "hood": 11991,
        "hopkins": 8442,
        "anderson": 7455,
        "chaney": 12731,
    },
    (date(2007, 11, 6), "Lee", 9): {"spell": 11054, "riley": 1563, "cole": 7192},
    (date(2007, 11, 6), "Lincoln", 7): {"pickering": 5634},
    (date(2007, 11, 6), "Madison", 3): {"eaves": 8254, "barbour": 18207, "bryant": 18201, "franks": 8197},
    (date(2007, 11, 6), "Madison", 5): {"hosemann": 18224, "smith": 8082, "hood": 13190, "hopkins": 13145},
    (date(2007, 11, 6), "Madison", 7): {"reeves": 18607, "pickering": 16983, "sumrall": 9147, "ohara": 7508},
    (date(2007, 11, 6), "Monroe", 7): {"riley": 988},
    (date(2007, 11, 6), "Montgomery", 7): {"chaney": 2275, "anderson": 2050, "riley": 284},
    (date(2007, 11, 6), "Oktibbeha", 5): {"hosemann": 5821, "smith": 4668, "hood": 6037, "hopkins": 4484},
    (date(2007, 11, 6), "Pearl River", 9): {"riley": 760},
    (date(2007, 11, 6), "Pike", 3): {"eaves": 5380, "barbour": 5594, "bryant": 5809, "franks": 5093},
    (date(2007, 11, 6), "Pike", 5): {"hosemann": 5735, "smith": 5117, "hood": 7118, "hopkins": 3766},
    (date(2007, 11, 6), "Pike", 7): {"sumrall": 5367, "pickering": 5459, "reeves": 6033, "ohara": 4743},
    (date(2007, 11, 6), "Quitman", 5): {"riley": 184},
    (date(2007, 11, 6), "Rankin", 15): {"spell": 21502},
    (date(2007, 11, 6), "Tallahatchie", 4): {"pickering": 1486},
    (date(2007, 11, 6), "Tallahatchie", 7): {"sumrall": 3037},
    (date(2007, 11, 6), "Tate", 5): {"chaney": 3891, "anderson": 2531, "riley": 491},
    (date(2007, 11, 6), "Tunica", 5): {"riley": 184},
    (date(2007, 11, 6), "Union", 3): {
        "sumrall": 3036,
        "hood": 4353,
        "reeves": 4656,
        "ohara": 2209,
        "pickering": 3900,
        "hopkins": 2727,
    },
    (date(2007, 11, 6), "Union", 4): {"riley": 485, "anderson": 2581, "chaney": 4414, "cole": 2366, "spell": 4105},
    (date(2007, 11, 6), "Washington", 3): {
        "sumrall": 5209,
        "hosemann": 3844,
        "smith": 5196,
        "bryant": 4015,
        "franks": 5150,
        "barbour": 4020,
        "eaves": 5289,
        "chaney": 3738,
        "anderson": 5428,
        "riley": 447,
        "spell": 3291,
        "cole": 5256,
        "hopkins": 2590,
        "hood": 6594,
    },
    (date(2007, 11, 6), "Washington", 5): {"reeves": 3920, "ohara": 5074, "pickering": 3795},
    (date(2007, 11, 6), "Winston", 5): {"riley": 358},
}
COUNTY_DIFFERENCES = {
    (date(2007, 11, 6), "Commissioner of Insurance", "Gary Anderson"): 2,
    (date(2007, 11, 6), "Commissioner of Insurance", "Mike Chaney"): -2,
}


def surname(name: str) -> str:
    words = [re.sub(r"[^a-z]", "", word.lower()) for word in name.replace(",", " ").split()]
    return [word for word in words if word and word not in {"jr", "sr"}][-1]


SURNAMES = {office: {surname(name): name for name, _ in names} for office, names in BALLOT.items()}
CANDIDATE = re.compile(r"^(\S.*?)\s{2,}([A-Z][a-z]+)\s+(\d[\d\s]*)$")


def county_scans() -> dict[tuple[date, str], str]:
    return {
        (day, county): COUNTY_SCAN.format(year=day.year, county=county.replace(" ", "%20"))
        for day, counties in COUNTY_SCANS.items()
        for county in counties
    }


def county_page(link: str, number: int) -> Path:
    scan = raw.cached(SOURCE, link)
    image = PAGES / f"{scan.sha256}-{number}.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-scale-to", "1650", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
            + [str(scan.path), str(image.with_suffix(""))],
            check=True,
        )
        with Image.open(image) as rendered:
            turns = {angle: rendered.rotate(angle, expand=True) for angle in (0, 90, 180, 270)}
        words = {angle: len(re.findall(r"[A-Za-z]{4,}", words_on(turned))) for angle, turned in turns.items()}
        best = max(words, key=words.__getitem__)
        if best and words[best] > 2 * words[0]:
            turns[best].save(image)
    return image


def words_on(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6"], input=buffer.getvalue(), capture_output=True, check=True
    ).stdout.decode(errors="replace")


def page_count(link: str) -> int:
    info = subprocess.run(
        ["pdfinfo", str(raw.cached(SOURCE, link).path)], capture_output=True, text=True, check=True
    ).stdout
    return int(re.findall(r"^Pages:\s+(\d+)", info, re.MULTILINE)[0])


def read_key(day: date, county: str, number: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{day}/{county}/{number}"


def read_counties() -> None:
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for (day, county), link in county_scans().items():
        for number in range(1, page_count(link) + 1):
            with Image.open(county_page(link, number)) as rendered:
                tile = rendered.copy()
            tile.thumbnail((LONGEST, LONGEST))
            buffer = io.BytesIO()
            tile.save(buffer, "PNG", optimize=True)
            params = MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=3000,
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
                            TextBlockParam(type="text", text=f"{county} County. Read the county-wide totals."),
                        ],
                    }
                ],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
            )
            wanted[read_key(day, county, number)] = (
                params,
                (tile.width * tile.height / 750 * price_in + 1000 * price_out) / 1e6,
            )
    print(batch.ensure(SOURCE, "county-totals-2007", wanted)[1])


PARTY_TAGS = {
    "dem": "Democrat",
    "democrat": "Democrat",
    "rep": "Republican",
    "republican": "Republican",
    "cns": "Constitution",
    "constitution": "Constitution",
}


def county_candidate(office: str, text: str) -> str | None:
    words = {re.sub(r"[^a-z]", "", word) for word in re.sub(r"[,()]", " ", text.lower()).split()}
    named = [name for names in SURNAMES.values() for name in names if name in words]
    if len(named) == 1:
        return named[0]
    close = {name for word in words for name in difflib.get_close_matches(word, list(SURNAMES[office]), 1, 0.6)}
    if len(close) == 1:
        return close.pop()
    parties = {PARTY_TAGS[word] for word in words if word in PARTY_TAGS}
    matched = [surname(name) for name, party in BALLOT[office] if party in parties]
    return matched[0] if len(parties) == 1 and len(matched) == 1 else None


@cache
def page_words(image: Path) -> tuple[int, int, pl.DataFrame]:
    with Image.open(image) as rendered:
        width, height = rendered.size
    words = pl.read_csv(
        io.StringIO(
            subprocess.run(
                ["tesseract", str(image), "stdout", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
            ).stdout
        ),
        separator="\t",
        quote_char=None,
        schema_overrides={"text": pl.String},
    ).with_columns(digits=pl.col("text").str.replace_all(r"[^0-9]", ""))
    return width, height, words


def locate(image: Path, votes: int) -> tuple[float, float, float, float]:
    width, height, words = page_words(image)
    found = words.filter(pl.col("digits") == str(votes))
    if not found.height:
        return (0.0, 0.0, 1.0, 1.0)
    left, top, wide, high = found.sort(pl.col("left") + pl.col("top")).select("left", "top", "width", "height").row(-1)
    return (left / width, top / height, (left + wide) / width, (top + high) / height)


def parse_counties() -> None:
    owner = {name: office for office, names in SURNAMES.items() for name in names}
    rows: list[dict[str, object]] = []
    for (day, county), link in county_scans().items():
        picked: dict[str, tuple[int, int]] = {}
        for number in range(1, page_count(link) + 1):
            message = json.loads(raw.cached(SOURCE, read_key(day, county, number)).path.read_text())
            texts = [block["text"] for block in message["content"] if block["type"] == "text"]
            try:
                totals = json.loads(texts[0])["totals"] if texts else []
            except json.JSONDecodeError:
                totals = []
            for total in totals:
                for cell in total["candidates"]:
                    if name := county_candidate(total["office"], cell["candidate"]):
                        picked[name] = (number, cell["votes"])
        for (when, place, number), cells in COUNTY_CORRECTED.items():
            if (when, place) == (day, county):
                picked |= {name: (number, votes) for name, votes in cells.items()}
        if missing := set(owner) - set(picked):
            raise ValueError(f"{day} {county}: no county total for {sorted(missing)}")
        for name, (number, votes) in picked.items():
            image = county_page(link, number)
            x0, y0, x1, y1 = locate(image, votes)
            rows.append(
                {
                    "day": day,
                    "office": owner[name],
                    "county": county,
                    "candidate": SURNAMES[owner[name]][name],
                    "votes": votes,
                    "page": number,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                }
            )
    frame = pl.DataFrame(rows)
    districts = pl.read_parquet(PARSED / "districts.parquet").filter(pl.col("day").is_in(list(COUNTY_SCANS)))
    summed = frame.group_by("day", "office", "candidate").agg(pl.col("votes").sum())
    printed = districts.group_by("day", "office", "candidate").agg(total=pl.col("votes").sum())
    checked = summed.join(printed, on=["day", "office", "candidate"], how="full", coalesce=True).with_columns(
        allowed=pl.struct("day", "office", "candidate").map_elements(
            lambda key: COUNTY_DIFFERENCES.get((key["day"], key["office"], key["candidate"]), 0),
            return_dtype=pl.Int64,
        )
    )
    if (
        wrong := checked.filter(pl.col("votes").is_null() | (pl.col("votes") - pl.col("total") != pl.col("allowed")))
    ).height:
        raise ValueError(f"county totals don't sum to the Electoral Vote Assignment totals: {wrong}")
    frame.write_parquet(PARSED / "counties.parquet")


def fetch() -> None:
    for link in [*FILES.values(), *REPORTS.values(), *SCANS.values(), SAMPLE_BALLOT, *county_scans().values()]:
        raw.fetch(SOURCE, link, suffix=".pdf")
    read_counties()


def pdf_text(link: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, link).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def parse_reports() -> None:
    rows: list[dict[str, object]] = []
    for day, link in REPORTS.items():
        districts: list[int] = []
        office: str | None = None
        printed: dict[str, tuple[int, int | None]] = {}
        for line in pdf_text(link).splitlines():
            if "MISSISSIPPI SECRETARY OF STATE" in line and "Rep " in line:
                districts = [int(found) for found in re.findall(r"Rep (\d+)", line)]
                continue
            if line.startswith("State Of Mississippi - "):
                office = line.removeprefix("State Of Mississippi - ").strip()
                continue
            found = re.match(r"^(\S.*?)\s{2,}([A-Z][a-z]+)\s+(\d[\d,.\s]*)$", line)
            if office != "Governor" or found is None:
                continue
            numbers = found[3].split()
            pairs = numbers[: 2 * len(districts)]
            if len(numbers) == 2 * len(districts) + 3:
                printed[surname(found[1])] = (int(numbers[-3].replace(",", "")), int(float(numbers[-2])))
            elif len(numbers) != 2 * len(districts):
                raise ValueError(f"{day} {found[1]}: {len(numbers)} numbers for districts {districts}")
            for district, votes, won in zip(districts, pairs[::2], pairs[1::2], strict=True):
                rows.append(
                    {
                        "day": day,
                        "office": "Governor",
                        "district": district,
                        "candidate": found[1],
                        "party": found[2],
                        "votes": int(votes.replace(",", "")),
                        "won": int(won),
                    }
                )
        check_report(day, pl.DataFrame(rows).filter(pl.col("day") == day), printed)
    frame = pl.concat([pl.DataFrame(rows), parse_scans()], how="diagonal_relaxed")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "districts.parquet")


def check_report(day: date, frame: pl.DataFrame, printed: dict[str, tuple[int, int | None]]) -> None:
    if sorted(set(frame["district"])) != list(range(1, 123)) or frame.height != 122 * len(printed):
        raise ValueError(f"{day}: districts {frame['district'].n_unique()}, rows {frame.height}, printed {printed}")
    summed = frame.group_by("candidate").agg(pl.col("votes").sum(), pl.col("won").sum())
    complete = sum(won or 0 for _, won in printed.values()) == 122
    for candidate, votes, won in summed.iter_rows():
        expected, districts = printed[surname(candidate)]
        if votes != expected or (complete and won != districts):
            raise ValueError(
                f"{day} {candidate}: {votes} votes and {won} districts, printed {expected} and {districts}"
            )
    leaders = frame.with_columns(
        top=pl.col("votes") == pl.col("votes").max().over("district"),
    )
    if leaders.filter(pl.col("top").cast(pl.Int64) != pl.col("won")).height:
        raise ValueError(
            f"{day}: a district's electoral vote is not its plurality: {leaders.filter(pl.col('top').cast(pl.Int64) != pl.col('won'))}"
        )


def scan_row(text: str) -> tuple[int, str, int, str, str | None] | None:
    tokens = text.split()
    if tokens[:4] != ["State", "House", "Of", "Rep"] or len(tokens) < 7 or not re.fullmatch(r"\d{3}", tokens[4]):
        return None
    at = 5
    while at < len(tokens) and re.fullmatch(r"[^\w'\"‘’“”]+", tokens[at]):
        at += 1
    start = at
    while at < len(tokens) and re.fullmatch(r"[A-Z'\"‘’“”][^\s\d]*", tokens[at]):
        at += 1
    if at == start or at == len(tokens):
        return None
    name = " ".join(tokens[start:at])
    if len(tokens[at]) == 1 and at + 1 < len(tokens) and re.fullmatch(r"\d{2,}", tokens[at + 1]):
        at += 1
    return int(tokens[4]), name, at, tokens[at], tokens[at + 1] if at + 1 < len(tokens) else None


def parse_scans() -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    PAGES.mkdir(parents=True, exist_ok=True)
    total = re.compile(r"^(.+?) (\d[\d,]*) (\S+) \S*%$")
    totals: dict[tuple[date, str], dict[str, tuple[int, int | None]]] = {}
    for day, link in SCANS.items():
        scan = raw.cached(SOURCE, link)
        info = subprocess.run(["pdfinfo", str(scan.path)], capture_output=True, text=True, check=True).stdout
        (count,) = re.findall(r"^Pages:\s+(\d+)", info, re.MULTILINE)
        for number in range(1, int(count) + 1):
            image = PAGES / f"{scan.sha256}-{number}.png"
            if not image.exists():
                subprocess.run(
                    ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(scan.path), str(image.with_suffix(""))],
                    check=True,
                )
            with Image.open(image) as rendered:
                width, height = rendered.size
            words = pl.read_csv(
                io.StringIO(
                    subprocess.run(
                        ["tesseract", str(image), "stdout", "--psm", "6", "tsv"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout
                ),
                separator="\t",
                quote_char=None,
                schema_overrides={"text": pl.String},
            ).filter(pl.col("conf") >= 0, pl.col("text").str.strip_chars() != "")
            lines = words.group_by("block_num", "par_num", "line_num", maintain_order=True).agg(
                "text", "left", "top", "width", "height"
            )
            texts = [" ".join(cells) for cells in lines["text"]]
            named = [surname(found[1]) for text in texts if (found := scan_row(text))]
            office = max(BALLOT, key=lambda office: sum(name in SURNAMES[office] for name in named))
            if sum(name in SURNAMES[office] for name in named) * 2 < len(named) or not named:
                raise ValueError(f"{day} page {number}: no office has the candidates {named}")
            printed = totals.setdefault((day, office), {})
            found_rows: list[
                tuple[tuple[int, str, int, str, str | None], list[int], list[int], list[int], list[int]]
            ] = []
            for text, (_, _, _, _, left, top, wide, high) in zip(texts, lines.iter_rows(), strict=True):
                if row := scan_row(text):
                    found_rows.append((row, left, top, wide, high))
                elif (found := total.match(text)) and not text.startswith("Candidate"):
                    printed[surname(found[1])] = (
                        int(found[2].replace(",", "")),
                        int(found[3]) if found[3].isdigit() else None,
                    )
            column = [
                (left[at], left[at] + wide[at])
                for (_, _, at, votes, _), left, _, wide, _ in found_rows
                if votes.isdigit()
            ]
            x0, x1 = min(a for a, _ in column), max(b for _, b in column)
            for (district, name, at, votes, read), left, top, wide, high in found_rows:
                box = (left[at], top[at], left[at] + wide[at], top[at] + high[at])
                if not votes.isdigit():
                    box = (x0, top[at - 1], x1, top[at - 1] + high[at - 1])
                    with Image.open(image) as rendered:
                        buffer = io.BytesIO()
                        rendered.crop((box[0] - 12, box[1] - 12, box[2] + 12, box[3] + 12)).save(buffer, "PNG")
                    votes = (
                        subprocess.run(
                            ["tesseract", "stdin", "stdout", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789"],
                            input=buffer.getvalue(),
                            capture_output=True,
                            check=True,
                        )
                        .stdout.decode()
                        .strip()
                    )
                rows.append(
                    {
                        "day": day,
                        "office": office,
                        "district": district,
                        "candidate": SURNAMES[office][surname(name)],
                        "votes": CORRECTED.get((day, office, district, surname(name)), int(votes)),
                        "read": {"1": 1, "0": 0}.get(read or ""),
                        "page": number,
                        "x0": box[0] / width,
                        "y0": box[1] / height,
                        "x1": box[2] / width,
                        "y1": box[3] / height,
                    }
                )
    frame = pl.DataFrame(rows).with_columns(
        won=(pl.col("votes") == pl.col("votes").max().over("day", "office", "district")).cast(pl.Int64)
    )
    if (wrong := frame.filter(pl.col("read").is_not_null() & (pl.col("read") != pl.col("won")))).height:
        raise ValueError(f"a printed electoral vote is not the district's plurality: {wrong}")
    for (day, office), printed in totals.items():
        check_report(day, frame.filter(pl.col("day") == day, pl.col("office") == office), printed)
    return frame.drop("read")


def parse() -> None:
    parse_reports()
    parse_counties()
    rows: list[dict[str, object]] = []
    for day, link in FILES.items():
        text = pdf_text(link)
        columns: list[str] = []
        placed: list[tuple[int, str]] = []
        extending = False
        office: str | None = None
        heading = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if heading:
                heading = False
                if office is not None and not CANDIDATE.match(line):
                    office = f"{office} {stripped}"
                    continue
            if (
                line.startswith(" ")
                and (extending or re.search(r"\S\s{4,}\S", stripped))
                and not re.search(r"[\d-]", stripped)
            ):
                found_names = [(match.start(), match[0]) for match in re.finditer(r"\S+(?: \S+)*", line)]
                placed = sorted([*placed, *found_names] if extending else found_names)
                columns = [text for _, text in placed]
                extending = True
                continue
            extending = False
            if line.startswith(" ") and "-" in stripped and not re.search(r"\d", stripped):
                parts = stripped.split("-", 1)
                office = parts[1] if parts[0] == "State Of Mississippi" else None
                heading = office is not None
                continue
            found = CANDIDATE.match(line)
            if office is None or found is None or not columns:
                continue
            counts = [int(count) for count in found[3].split()]
            if len(counts) != len(columns):
                raise ValueError(f"{office}: {found[1]} has {len(counts)} counts for {len(columns)} columns")
            for county, count in zip(columns, counts, strict=True):
                if county == "TOTAL":
                    continue
                rows.append(
                    {
                        "day": day,
                        "office": office,
                        "candidate": found[1],
                        "party": found[2],
                        "county": county,
                        "votes": count,
                    }
                )
            if columns[-1] == "TOTAL":
                rows.append(
                    {
                        "day": day,
                        "office": office,
                        "candidate": found[1],
                        "party": found[2],
                        "county": None,
                        "votes": counts[-1],
                    }
                )
    frame = pl.DataFrame(rows)
    unknown = set(frame["office"]) - set(OFFICES)
    if unknown:
        raise ValueError(f"unknown offices: {unknown}")
    counted = (
        frame.filter(pl.col("county").is_not_null()).group_by("day", "office", "candidate").agg(pl.col("votes").sum())
    )
    printed = frame.filter(pl.col("county").is_null()).select("day", "office", "candidate", total="votes")
    checked = counted.join(printed, on=["day", "office", "candidate"], how="full", coalesce=True)
    if checked.filter(pl.col("votes") != pl.col("total")).height or checked["total"].null_count():
        raise ValueError(
            f"counties don't sum to the printed totals: {checked.filter(pl.col('votes') != pl.col('total'))}"
        )
    counties = frame.filter(pl.col("county").is_not_null()).group_by("day", "office").agg(pl.col("county").n_unique())
    if counties.filter(pl.col("county") != 82).height:
        raise ValueError(f"offices without 82 counties: {counties.filter(pl.col('county') != 82)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("county").is_not_null()).with_columns(
        office=pl.col("office").replace_strict(OFFICES)
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mississippi's statewide general elections of 2023 by county, the 2015 and 2019 governor elections"
            " by House district, and the 2007 statewide elections by House district and county",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.ms.gov/elections-voting/election-results",
            license="Public record",
        )
        for link in FILES.values():
            cs.artifact(raw.cached(SOURCE, link), source)
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-MS'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties: dict[str, UUID] = {
            re.sub(r"[^a-z]", "", text.lower()): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '28%'"""
            ).fetchall()
        }
        counties["jeffdavis"] = counties["jeffersondavis"]
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the fptp system and the national parties must exist")
        persons: dict[str, UUID] = {}
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            election_key = f"{day}:MS"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-MS"),
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} Mississippi general election")
                cs.add("slug", path=f"us/ms/{day}-general", entity_id=election)
            office_slug = slug(office)
            office_id = cs.key("us.office", f"{office_slug}:MS")
            if f"{office_slug}:MS" not in offices_known:
                offices_known.add(f"{office_slug}:MS")
                cs.add(
                    "office",
                    id=office_id,
                    polity_id=cs.polity("US-MS"),
                    kind="governor" if office == "Governor" else "other",
                )
                name(office_id, f"{office} of Mississippi")
            contest_key = f"{day}:MS:{office_slug}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Mississippi {office} election")
            cs.add("slug", path=f"us/ms/{day}-general/{office_slug}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            totals: dict[UUID, int] = {}
            for (candidate, label), cells in lines.group_by("candidate", "party", maintain_order=True):
                if candidate not in persons:
                    persons[candidate] = cs.key(f"{SOURCE}.person", f"MS:{slug(candidate)}")
                    if persons[candidate] in cs.minted:
                        cs.add("person", id=persons[candidate])
                        name(persons[candidate], candidate)
                party = parties.get(label)
                option = cs.key("us.option", f"{contest_key}:{persons[candidate]}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{persons[candidate]}:head"),
                    person_id=persons[candidate],
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=party is None,
                )
                totals[option] = int(cells["votes"].sum())
                for county, votes in cells.select("county", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[re.sub(r"[^a-z]", "", county.lower())],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            leader = max(totals, key=totals.__getitem__)
            if totals[leader] * 2 > sum(totals.values()):
                cs.add("outcome", result_set_id=result_set, option_id=leader, kind="elected", seats=1)
        attach_reports(cs, conn)
        return cs.write()


def attach_reports(cs: Changeset, conn: Connection) -> None:
    frame = pl.read_parquet(PARSED / "districts.parquet")
    source = cs.source(PUBLISHER, REPORT_TITLE)
    cs.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=REPORT_TITLE,
        url="https://www.sos.ms.gov/elections-voting/election-results",
        license="Public record",
    )
    for link in REPORTS.values():
        cs.artifact(raw.cached(SOURCE, link), source)
    pages: dict[tuple[date, int], UUID] = {}
    for day, link in SCANS.items():
        scan = raw.cached(SOURCE, link)
        artifact = cs.artifact(scan, source)
        for (number,) in frame.filter(pl.col("day") == day).select("page").unique().sort("page").iter_rows():
            image = PAGES / f"{scan.sha256}-{number}.png"
            with Image.open(image) as rendered:
                width, height = rendered.size
            pages[(day, number)] = cs.page(artifact, number)
            cs.add(
                "source_page",
                id=pages[(day, number)],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
    ballot = cs.source(PUBLISHER, "Official sample ballot")
    cs.add(
        "source",
        id=ballot,
        kind="official_body",
        publisher=PUBLISHER,
        title="Official sample ballot",
        url="https://www.sos.ms.gov/elections-voting",
        license="Public record",
    )
    cs.artifact(raw.cached(SOURCE, SAMPLE_BALLOT), ballot)
    people: dict[str, set[UUID]] = {}
    for person, ballot_name in conn.execute(
        """select distinct ca.person_id, ca.ballot_name from candidacy ca
           join contest_option co on co.id = ca.option_id
           join contest c on c.id = co.contest_id
           join office o on o.id = c.office_id
           join polity p on p.id = o.polity_id and p.iso_code = 'US-MS'"""
    ).fetchall():
        people.setdefault(re.sub(r"[^a-z]", "", ballot_name.lower()), set()).add(person)
    fptp = cs.key("us.electoral_system", "fptp")
    parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
    if cs.minted & {fptp, *parties.values()}:
        raise ValueError("the fptp system and the national parties must exist")
    ((state, area),) = conn.execute(
        """select u.id, u.area_id from reporting_unit u
           join polity_territory t on t.area_id = u.area_id
           join polity p on p.id = t.polity_id and p.iso_code = 'US-MS'
           where u.scheme_code = 'us.state'"""
    ).fetchall()
    cs.add(
        "scheme",
        code=DISTRICTS,
        polity_id=cs.polity("US-MS"),
        kind="electoral_district",
        rank=3,
        noun="house district",
    )
    counties = pl.read_parquet(PARSED / "counties.parquet")
    returns = cs.source(PUBLISHER, COUNTY_TITLE)
    cs.add(
        "source",
        id=returns,
        kind="official_body",
        publisher=PUBLISHER,
        title=COUNTY_TITLE,
        url="https://www.sos.ms.gov/elections-voting/election-results",
        license="Public record",
    )
    county_pages: dict[tuple[date, str, int], UUID] = {}
    for (day, county), link in county_scans().items():
        artifact = cs.artifact(raw.cached(SOURCE, link), returns)
        for (number,) in (
            counties.filter(pl.col("day") == day, pl.col("county") == county).select("page").unique().iter_rows()
        ):
            image = county_page(link, number)
            with Image.open(image) as rendered:
                width, height = rendered.size
            county_pages[(day, county, number)] = cs.page(artifact, number)
            cs.add(
                "source_page",
                id=county_pages[(day, county, number)],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
    county_units: dict[str, UUID] = {
        re.sub(r"[^a-z]", "", text.lower()): unit
        for text, unit in conn.execute(
            """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '28%'"""
        ).fetchall()
    }
    official = {
        contest
        for (contest,) in conn.execute(
            """select rs.contest_id from result_set rs
               join source s on s.id = rs.source_id and s.kind = 'official_body'
               join contest c on c.id = rs.contest_id
               join office o on o.id = c.office_id
               join polity p on p.id = o.polity_id and p.iso_code = 'US-MS'
               where rs.status = 'active' and rs.changeset_id <> all(
                   select id from changeset where pipeline = %s)
                 and exists (select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                             where v.result_set_k = rs.k and u.scheme_code = 'us.county')""",
            [SOURCE],
        ).fetchall()
    }
    placed: set[date] = set()
    for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
        office_slug = slug(office)
        contest_key = f"{day}:MS:{office_slug}"
        if office == "Governor":
            ((contest, election),) = conn.execute(
                """select c.id, c.election_id
                   from contest c join election e on e.id = c.election_id
                   join office o on o.id = c.office_id and o.kind = 'governor'
                   join polity p on p.id = o.polity_id and p.iso_code = 'US-MS'
                   where e.date_from = %s and c.round_number = 1""",
                [day],
            ).fetchall()
            options: dict[str, UUID] = {
                surname(label): option
                for option, label in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
        else:
            election = cs.key("us.election", f"{day}:MS")
            office_id = cs.key("us.office", f"{office_slug}:MS")
            contest = cs.key("us.contest", contest_key)
            if cs.minted & {election, office_id}:
                raise ValueError(f"{contest_key}: the election and office must exist")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{day.year} Mississippi {office} election",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"us/ms/{day}-general/{office_slug}", entity_id=contest)
            options = {}
            for candidate, label in BALLOT[office]:
                known = people.get(re.sub(r"[^a-z]", "", candidate.lower()), set())
                person = next(iter(known)) if len(known) == 1 else cs.key(f"{SOURCE}.person", f"MS:{slug(candidate)}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True)
                option = cs.key("us.option", f"{contest_key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties[label],
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=False,
                )
                options[surname(candidate)] = option
        result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:{DISTRICTS}")
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=contest not in official and day not in COUNTY_SCANS,
            completeness=1,
        )
        if day in COUNTY_SCANS:
            county_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:us.county")
            cs.add(
                "result_set",
                id=county_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=returns,
                is_default=True,
                completeness=1,
            )
            cells = counties.filter(pl.col("day") == day, pl.col("office") == office)
            for county, candidate, votes, number, x0, y0, x1, y1 in cells.select(
                "county", "candidate", "votes", "page", "x0", "y0", "x1", "y1"
            ).iter_rows():
                fact = {
                    "result_set_id": county_set,
                    "unit_id": county_units[re.sub(r"[^a-z]", "", county.lower())],
                    "option_id": options[surname(candidate)],
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=county_pages[(day, county, number)],
                    source_id=None,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )
        if office != "Governor":
            summed = lines.group_by("candidate").agg(pl.col("votes").sum()).sort("votes", descending=True)
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[surname(summed["candidate"][0])],
                kind="elected",
                seats=1,
            )
        for district in range(1, 123) if day not in placed else []:
            code = f"{day.year}-{district:03d}"
            cs.add(
                "reporting_unit",
                id=cs.unit(DISTRICTS, code),
                polity_id=cs.polity("US-MS"),
                scheme_code=DISTRICTS,
                code=code,
                parent_id=state,
                area_relation="equals",
                area_id=cs.area("us.sldl", f"MS:{district}"),
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        placed.add(day)
        for candidate, district, votes, number, *box in lines.select(
            "candidate", "district", "votes", "page", "x0", "y0", "x1", "y1"
        ).iter_rows():
            fact = {
                "result_set_id": result_set,
                "unit_id": cs.unit(DISTRICTS, f"{day.year}-{district:03d}"),
                "option_id": options[surname(candidate)],
                "channel": 0,
                "measure": 1,
                "count_round": 0,
            }
            cs.add("result_value", **fact, flags=0 if number is None else house_books.DIGITIZED, value=votes)
            if number is not None:
                x0, y0, x1, y1 = box
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=pages[(day, number)],
                    source_id=None,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )
