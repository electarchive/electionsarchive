import difflib
import re
import subprocess
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.nm_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New Mexico Secretary of State"
TITLE = "Official 1998 General Election Results by county"
LANDING = "https://www.sos.nm.gov/sos-archive/election-results-archive/1998-election-results/"
BASE = "https://klvg4oyd4j.execute-api.us-west-2.amazonaws.com/prod/PublicFiles/ee3072ab0d43456cb15a51f7d82c77a2/"
FILES = (
    "6a0429af-2180-44e8-8772-ebbdefa083ae/BernalilloGen98.htm",
    "72762bfe-fca0-4d69-afbc-736e535c1141/CatronGen98.htm",
    "959bf77a-4638-47cb-88cc-5584a512213a/ChavesGen98.htm",
    "4e02c377-76cd-472b-9a4f-4982b99823b1/CibolaGen98.htm",
    "29e1918c-c4e1-4a7b-9312-e939529783be/ColfaxGen98.htm",
    "f8957b7b-4dbb-4e73-91bd-8855fa2c53a1/CurryGen98.htm",
    "29fbc8a2-931d-42fb-904d-f23d3e73c104/conty026.htm",
    "76e9b8f8-6122-4fb9-809b-cb5fe10f6bcc/DonaAnaGen98.htm",
    "e3ddd50d-7b9d-4e0c-8418-6a8e3c1d821b/EddyGen98.htm",
    "7869bd85-8c05-4912-9709-00e1f6b519fe/GrantGen98.htm",
    "91f48a44-e63a-4121-ad5d-52ae043e4cbe/conty023.htm",
    "1d7eb198-2976-460d-ab42-27766b120014/conty030.htm",
    "6029c462-77d1-4f41-8ab7-6b6e85643b0a/conty022.htm",
    "fc0d2097-cffc-4541-8197-8cf761efeab6/LeaGen98.htm",
    "a265c16a-1e5d-4288-9065-02de1a736aae/conty025.htm",
    "42dbac3d-b05a-4fec-84af-3b9445e3ba96/conty031.htm",
    "c082f998-0a6e-44e4-94f1-e4a3d753ebce/conty018.htm",
    "2f432b84-f328-4117-a65c-1074b84a2971/conty012.htm",
    "9c25c298-73ff-4b5e-a4f1-9d478c5d9aa0/conty029.htm",
    "0a57be7a-3dc0-40f7-be0d-e81608768ddd/conty014.htm",
    "bce386a9-3f45-47fb-82ca-8e5ebd79074d/QuayGen98.htm",
    "8da2cccb-ba6f-4483-8958-3fefcc564971/conty016.htm",
    "affe0ce4-a3ef-4364-850e-712f4c6b1ea7/RooseveltGen98.htm",
    "12613dd3-7aa4-4a81-8feb-f23f59e615ae/conty015.htm",
    "e2fff329-d442-4f8c-9621-e8307869acae/conty011.htm",
    "d8365e68-9807-4707-b81d-a820f57d12ee/conty028.htm",
    "182119ae-e1bb-42c7-abb9-7bba3709a357/SantaFeGen9.htm",
    "b1468375-47a0-485a-a14d-e26b9d12cf40/conty020.htm",
    "c272f3db-e0b1-41fe-aab3-77d7a201618b/conty024.htm",
    "6e369702-7ec0-4158-9204-40f2e8a32dfe/conty019.htm",
    "74a2d692-a37a-410f-9e8c-928db6021109/conty021.htm",
    "cff7ee83-dcc5-4faa-a574-7bbc0a9ca753/conty017.htm",
    "214aee75-d4b7-4084-bd39-26989e95a06a/conty013.htm",
)
DAY = date(1998, 11, 3)
METHOD = (
    "County totals are from the Secretary of State's official county pages for the November 3, 1998 general election "
    "(as of December 4, 1998), one page per county. Counties split between districts report each district's part."
)


def fetch() -> None:
    for line in FILES:
        url = BASE + line
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            page = subprocess.run(
                ["curl", "-sL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, page, suffix=".htm", retrieved_at=datetime.now(UTC), media_type="text/html")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '35%%'"""
            ).fetchall()
        }
    rows = []
    for line in FILES:
        page = raw.cached(SOURCE, BASE + line).path.read_bytes().decode("latin-1")
        body = page[max(page.lower().find("<body"), 0) :]
        name = re.search(r"Results for(?:<BR>|\s)+([A-Za-z .]+?)\s*</font>", body, re.IGNORECASE)[1]
        key = re.sub(r"[^a-z]", "", name.lower())
        key = key if key in counties else difflib.get_close_matches(key, counties, 1, 0.7)[0]
        district = 0
        tokens = [re.sub(r"\s+", " ", unescape(token)).strip() for token in re.split(r"<[^>]*>", body) if token.strip()]
        district = 0
        name = None
        votes = None
        for token in tokens:
            heading = re.search(r"UNITED STATES REPRESENTATIVE - DISTRICT 0(\d)", token)
            if heading:
                district, name, votes = int(heading[1]), None, None
            elif re.search(
                r"(General Election Results for|^[A-Z ]+ - DISTRICT|^(GOVERNOR|SECRETARY|STATE|ATTORNEY|COMMISSIONER))",
                token,
            ):
                district, name, votes = 0, None, None
            elif district and token.endswith("%"):
                rows.append((str(DAY), district, counties[key], name, votes))
                name, votes = None, None
            elif district and re.fullmatch(r"[\d,]+", token):
                votes = int(token.replace(",", ""))
            elif district and name is None and not token.endswith(" County"):
                name = token
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("district").agg(pl.col("county").n_unique(), pl.col("column").n_unique()).sort("district"))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "New Mexico's U.S. House general election of 1998 by county from the Secretary of State's county pages",
        PUBLISHER,
        TITLE,
        LANDING,
        "NM",
        "35",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, BASE + line) for line in FILES],
        lambda key: METHOD,
    )
