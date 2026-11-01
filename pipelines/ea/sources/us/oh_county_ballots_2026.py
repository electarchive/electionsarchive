import json
import re
import subprocess
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.oh_county_ballots_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
INBOX = DATA / "inbox" / "manual" / "MF-370"
HAMILTON = "https://www.hamiltoncountyohio.gov/government/departments/board_of_elections"
HAMILTON_FILE = INBOX / "hamilton" / "November-2026-Ballot-Issues.pdf"
SUMMIT = "https://www.summitcountyohio.gov/boe/election-60-day-issue-synopsis"
SUMMIT_FILE = INBOX / "summit" / "Election_60IssueSynopsis.pdf"
CUYAHOGA = "https://boe.cuyahogacounty.gov/elections/GetDocumentById/0ed80c8b-5b7d-46d5-b60f-91a47eea555b/"
WAYNE_FILE = "https://www.waynecountyoh.gov/wp-content/uploads/2026/09/Sample-Ballots-ALL-WATERMARK-09192026-1352.pdf"
FRANKLIN = "https://vote.franklincountyohio.gov"
FRANKLIN_PAGE = f"{FRANKLIN}/election-info/2026"
FRANKLIN_DIR = INBOX / "franklin" / "notices"
LORAIN_FILE = "https://www.voteloraincountyohio.gov/_files/ugd/2568d0_fd0d57cfa16c443899cf8c9653a28945.pdf"
MF = DATA / "inbox" / "manual" / "MF-540"
MF_LOOKUP = (
    "Ashtabula Athens Tuscarawas Mahoning Licking Perry Clark Greene Harrison Geauga Richland Fulton Portage Lucas"
).split()
MF_555 = DATA / "inbox" / "manual" / "MF-555"
MF_555_COUNTIES = [
    "Meigs",
    "Medina",
    "Adams",
    "Belmont",
    "Clinton",
    "Columbiana",
    "Delaware",
    "Erie",
    "Guernsey",
    "Lake",
    "Preble",
    "Putnam",
]
MF_559 = DATA / "inbox" / "manual" / "MF-559"
MF_559_COUNTIES = [
    "Morrow",
    "Holmes",
    "Madison",
    "Knox",
    "Hocking",
    "Muskingum",
    "Ottawa",
    "Huron",
    "Marion",
    "Scioto",
    "Pike",
]
MF_561 = DATA / "inbox" / "manual" / "MF-561"
MF_561_COUNTIES = ["Darke"]
MF_562 = DATA / "inbox" / "manual" / "MF-562"
MF_562_COUNTIES = [
    "Defiance",
    "Fairfield",
    "Fayette",
    "Gallia",
    "Hancock",
    "Hardin",
    "Henry",
    "Highland",
    "Jackson",
]
MF_563 = DATA / "inbox" / "manual" / "MF-563"
MF_563_COUNTIES = [
    "Lawrence",
    "Logan",
    "Miami",
    "Monroe",
    "Morgan",
    "Noble",
    "Paulding",
    "Pickaway",
]
MF_564 = DATA / "inbox" / "manual" / "MF-564"
MF_564_COUNTIES = [
    "Ross",
    "Seneca",
    "Shelby",
    "Union",
    "Van Wert",
    "Warren",
    "Washington",
    "Williams",
    "Wyandot",
]
MF_568 = DATA / "inbox" / "manual" / "MF-568"
MF_568_COUNTIES = [
    "Allen",
    "Ashland",
    "Auglaize",
    "Brown",
    "Carroll",
    "Champaign",
    "Coshocton",
]
MF_LOOKUP = [
    *MF_LOOKUP,
    *MF_555_COUNTIES,
    *MF_559_COUNTIES,
    *MF_561_COUNTIES,
    *MF_562_COUNTIES,
    *MF_563_COUNTIES,
    *MF_564_COUNTIES,
    *MF_568_COUNTIES,
]
MF_SAMPLES = [*MF_LOOKUP, "Sandusky"]
MONTGOMERY_ZIP = INBOX / "montgomery" / "oh-2026-general-montgomery-sample-ballots.zip"
MONTGOMERY_KEY = "https://lookup.boe.ohio.gov/vtrapp/montgomery/ballotlist.aspx#sample-ballots.zip"
WATERMARK = re.compile(r"(?:\s+[elpmaS])(?:\s+[elpmaS]){3,}(?=\s|$)")
DROPPED = {
    "he": "the",
    "axable": "taxable",
    "eported": "reported",
    "ervices": "services",
    "estaurant": "restaurant",
    "ime": "time",
    "irm": "firm",
    "irst": "first",
    "mposed": "imposed",
    "mprovements": "improvements",
    "ntent": "intent",
    "nterested": "interested",
    "ocial": "social",
    "efferson": "Jefferson",
}
DROPPED_PATTERN = re.compile(
    r"(?<![\w/])(?:%s)(?![\w])|(?<![\w/])n(?= (?:Council|this precinct|his precinct))|(?<=in )his(?= precinct)|(?<=\. )o(?= the City)"
    % "|".join(DROPPED)
)
LEAD = re.compile(r"^(?:Sample\s*)*\d+\s+")
SOURCES = {
    HAMILTON: (
        "Hamilton",
        HAMILTON_FILE,
        "Hamilton County Board of Elections",
        "November 3, 2026 General Election ballot issues",
    ),
    SUMMIT: (
        "Summit",
        SUMMIT_FILE,
        "Summit County Board of Elections",
        "Issue synopsis, November 3, 2026 General Election",
    ),
    CUYAHOGA: (
        "Cuyahoga",
        None,
        "Cuyahoga County Board of Elections",
        "Issues list, November 3, 2026 General Election",
    ),
    WAYNE_FILE: (
        "Wayne",
        None,
        "Wayne County Board of Elections",
        "Sample ballots, November 3, 2026 General Election",
    ),
    LORAIN_FILE: (
        "Lorain",
        None,
        "Lorain County Board of Elections",
        "Ballot text, November 3, 2026 General Election",
    ),
}
NOISE = re.compile(
    r"^(Sample Generated|\d\d-\d\d-|Page \d+ of|VOTE BOTH SIDES|Members of the|November 3, 2026$|B A L L O\s*$|S A M P L E|\d+$)"
)
STOP = re.compile(r"^(YES|NO|SI|PAGE \d+ OF.*|FOR\b.*|AGAINST\b.*|A FAVOR\b.*|EN CONTRA\b.*)$")
OPTION = re.compile(r"^(?:For|Against)\b", re.IGNORECASE)
HINTS = {
    "Worthington-Charter-Amendment-Referendum-Petition-Signature-Requirement-(by-Petition)": "Charter Amendment: 1.05 - Petition",
    "Worthington-Charter-Amendment-Referendum-Petition-Signature-Requirement-(Commission)": "Charter Amendment: Section 1.05 - Commission",
    "Worthington-Charter-Amendment-Effective-Date-of-Ordinances-(Commission)": "Charter Amendment: 1.04, 2.17, 2.19",
}
ALIASES = {"Stark County District Library": "Stark County"}
COUNTY_OF = {"Stark County District Library": "Stark"}
SCHOOLS = "us.oh_questions_2026"
DIRECTORY = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory/2022/?fips=39"
PRECINCT = re.compile(r"(?<=[a-z])\s+(?:\d+-?[A-Z]|[A-Z])$|(?<=[A-Z])\s+\d+-[A-Z]$")
START = re.compile(
    r"^(?:Sample\s*)*(?:[#]?\d+[.]?(?:\s\d)?\s*)?(?:T)?(Proposed|PROPOSED|Special Election|SPECIAL ELECTION|Advisory|ADVISORY|Local|LOCAL)\b"
)


def fetch() -> None:
    for url, (_, path, _, _) in SOURCES.items():
        if path is None:
            raw.fetch(SOURCE, url, suffix=".pdf")
            continue
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf",
            )
    try:
        raw.cached(SOURCE, MONTGOMERY_KEY)
    except LookupError:
        raw.store(
            SOURCE,
            MONTGOMERY_KEY,
            MONTGOMERY_ZIP.read_bytes(),
            suffix=".zip",
            retrieved_at=datetime.fromtimestamp(MONTGOMERY_ZIP.stat().st_mtime, UTC),
            media_type="application/zip",
        )
    fetch_franklin()
    for county, path in mf_files():
        key = mf_key(county, path)
        try:
            raw.cached(SOURCE, key)
        except LookupError:
            raw.store(
                SOURCE,
                key,
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def mf_dir(county: str) -> Path:
    if county in MF_568_COUNTIES:
        return MF_568
    if county in MF_564_COUNTIES:
        return MF_564
    if county in MF_563_COUNTIES:
        return MF_563
    if county in MF_562_COUNTIES:
        return MF_562
    if county in MF_561_COUNTIES:
        return MF_561
    if county in MF_559_COUNTIES:
        return MF_559
    return MF_555 if county in MF_555_COUNTIES else MF


def mf_files() -> list[tuple[str, Path]]:
    return [
        (county, path)
        for county in [*MF_SAMPLES, "Stark"]
        for path in sorted((mf_dir(county) / county.lower().replace(" ", "")).glob("*.pdf"))
        if county != "Stark" or "legal ad" in path.name
    ]


def mf_page(county: str) -> str:
    if county in MF_LOOKUP:
        return f"https://lookup.boe.ohio.gov/vtrapp/{county.lower().replace(" ", "")}/ballotlist.aspx"
    return f"https://www.boe.ohio.gov/{county.lower()}"


def mf_key(county: str, path: Path) -> str:
    return f"{mf_page(county)}#{path.name}"


def mf_meta(county: str, path: Path) -> dict[str, str]:
    kind = "Legal notice" if county == "Stark" else "Sample ballot"
    return {
        "publisher": f"{county} County Board of Elections",
        "title": f"{kind} {path.stem}, November 3, 2026 General Election",
    }


def franklin_notices() -> list[str]:
    html = raw.cached(SOURCE, FRANKLIN_PAGE).path.read_text()
    return sorted({FRANKLIN + path for path in re.findall(r'href="(/getmedia/[^"]*Notice-of-Election[^"]*)"', html)})


def franklin() -> list[dict[str, str]]:
    rows = []
    for url in franklin_notices():
        text = re.sub(r"\s+", " ", pdf(url, "-layout"))
        if "3rd day of November, 2026" not in text:
            continue
        entity = None
        for pattern in (
            r"vote of the people of (?:the )?(.+?) at the",
            r"Resolution of the (?:Township Trustees|City Council|Council) of (?:the )?(.+?) (?:of Franklin County, )?Ohio",
            r"Board of Education of the (.+? School District)",
        ):
            found = re.search(pattern, text)
            if found:
                entity = re.sub(r"(?: of Franklin County)?(?:, Ohio)?$", "", found[1]).strip(" ,")
                break
        question = re.search(r"the question of:? (.+?) The polls for the election", text)
        if entity is None or question is None:
            print("unparsed notice:", url)
            continue
        body = question[1].strip()
        heading = text.split("R.C.")[0].lower()
        if "bonds" in heading:
            kind = "BOND"
        elif "tax levy" in heading:
            kind = "TAX LEVY " + " ".join(
                word
                for word in ("additional", "renewal", "replacement", "decrease", "increase")
                if word in body[:600].lower()
            )
        elif "bonds" in body.lower() and "tax" in body.lower():
            kind = "COMBINATION"
        elif "zoning" in body.lower():
            kind = "ZONING"
        elif "aggregation" in url.lower():
            kind = "GAS"
        else:
            kind = "CHARTER"
        rows.append(
            {
                "url": url,
                "county": "Franklin",
                "kind": kind,
                "entity": entity,
                "text": body,
                "hint": next((h for k, h in HINTS.items() if k in url), ""),
            }
        )
    return rows


def fetch_franklin() -> None:
    try:
        raw.cached(SOURCE, FRANKLIN_PAGE)
    except LookupError:
        raw.fetch(SOURCE, FRANKLIN_PAGE, suffix=".html")
    for url in franklin_notices():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix=".pdf")


def pdf(url: str, mode: str) -> str:
    return subprocess.run(
        ["pdftotext", mode, str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout.replace("\f", "\n")


def hamilton() -> list[dict[str, str]]:
    rows = []
    for block in re.split(r"^\s*\d+ (?=PROPOSED|LOCAL|Proposed|Local)", pdf(HAMILTON, "-layout"), flags=re.MULTILINE)[
        1:
    ]:
        head, _, rest = block.partition("A majority affirmative vote is necessary for passage.")
        lines = [line.strip() for line in head.split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        entity = lines[-1]
        body = re.split(r"\n\s*(?:FOR|AGAINST|YES|NO|Yes|No)\b", rest)[0]
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            rows.append(
                {"url": HAMILTON, "county": "Hamilton", "kind": " ".join(lines[:-1]), "entity": entity, "text": body}
            )
    return rows


def summit() -> list[dict[str, str]]:
    rows = []
    for block in re.split(r"^Issue \d+\s*$", pdf(SUMMIT, "-raw"), flags=re.MULTILINE)[1:]:
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        text = re.split(r"THIS LIST IS SUBJECT", "\n".join(lines))[0].split("\n")
        entity: list[str] = []
        index = 0
        while index < len(text) and not re.fullmatch(r"[A-Z ,&-]+|\([A-Z, ]+\)", text[index]):
            entity.append(text[index])
            index += 1
        kind: list[str] = []
        while index < len(text) and (
            re.fullmatch(r"[A-Z ,&-]+|\([A-Z, ]+\)", text[index])
            or text[index].startswith(("Overlaps", "Counties", "Overlap"))
        ):
            kind.append(text[index])
            index += 1
        body = re.sub(r"\s+", " ", " ".join(text[index:])).strip()
        if entity and body and not entity[0].startswith("STATE"):
            rows.append(
                {
                    "url": SUMMIT,
                    "county": "Summit",
                    "kind": " ".join(kind).replace("Overlaps:", ""),
                    "entity": re.sub(r"\s*MUNICIPAL / COUNTY$", "", " ".join(entity)),
                    "text": body,
                }
            )
    return rows


def cuyahoga() -> list[dict[str, str]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CUYAHOGA).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.replace("\f", "\n")
    rows = []
    for block in re.split(r"^\d+\s+[A-Z0-9]{5,6}\s{2,}", text, flags=re.MULTILINE)[1:]:
        head, _, rest = block.partition("\n")
        if "COUNTY WIDE" in head and "Constitutional" in rest:
            continue
        place = re.sub(r"\s*-\d\d-\s*[A-Z]$", "", head.strip())
        chunks = re.split(r"\n\s*\n", re.split(r"^\s*\w+ Issues / Asuntos", rest, flags=re.MULTILINE)[0])
        chunks = [c for c in chunks if c.strip() and not re.fullmatch(r"\s*Page \d+ of \d+\s*", c)]
        paragraphs = [re.sub(r"\s+", " ", c).strip() for c in chunks]
        spanish = next((i for i, p in enumerate(paragraphs) if p.startswith("Se requiere")), None)
        if spanish is None or spanish < 4 or not paragraphs[1].startswith("A majority affirmative"):
            print("cuyahoga skipped:", head.strip())
            continue
        lines = [line.strip() for line in chunks[0].splitlines() if line.strip()]
        if lines[0].startswith(("Special", "Local", "Advisory")):
            kind, entity = " ".join(lines), place
        else:
            count = 2 if len(lines) > 1 and lines[1].startswith("(") else 1
            kind, entity = " ".join(lines[:count]), "|".join([*lines[count:], place])
            entity = entity.replace("County of Cuyahoga", "Cuyahoga County")
        rows.append(
            {
                "url": CUYAHOGA,
                "county": "Cuyahoga",
                "kind": kind,
                "entity": entity,
                "text": " ".join(paragraphs[2 : spanish - 1]),
            }
        )
    return rows


def body_text(body: list[str]) -> str:
    return " ".join(body).strip()


def opens_issue(lines: list[str], cursor: int) -> bool:
    return START.match(lines[cursor]) is not None and any(
        line.startswith("A majority affirmative vote") for line in lines[cursor + 1 : cursor + 5]
    )


def ballot_issues(lines: list[str], url: str, county: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in lines if line.strip() and not NOISE.match(line.strip())]
    rows = []
    seen = set()
    for index, line in enumerate(lines):
        if not line.startswith("A majority affirmative vote"):
            continue
        head: list[str] = []
        cursor = index - 1
        while cursor >= 0 and len(head) < 7 and not STOP.match(lines[cursor]):
            head.append(lines[cursor])
            cursor -= 1
        head.reverse()
        for offset, text in enumerate(head):
            if START.match(text):
                head = head[offset:]
                break
        else:
            print("no header:", county, head)
            continue
        head[0] = re.sub(
            r"^(?:Sample\s*)*(?:#?\d+\.?(?:\s\d)?\s*)?T?(?=Proposed|PROPOSED|Special|SPECIAL|Advisory|ADVISORY|Local|LOCAL)",
            "",
            head[0],
        )
        cursor = index + 1
        body = []
        for ahead in range(index, min(index + 3, len(lines))):
            if "passage" in lines[ahead]:
                cursor = ahead + 1
                body.append(re.sub(r"^.*?passage\.?\s*", "", lines[ahead], count=1))
                break
        while cursor < len(lines):
            leading = body_text(body) == "" and OPTION.match(lines[cursor])
            if not leading and (STOP.match(lines[cursor]) or opens_issue(lines, cursor)):
                break
            if not leading:
                body.append(lines[cursor])
            cursor += 1
        text = re.sub(r"\s+", " ", " ".join(body)).strip()
        text = re.split(r"\s*(?:For the (?:Tax )?\w+ Against|S a m p l e|This sample ballot was produced)", text)[
            0
        ].strip()
        kind = head[0] + (" " + head[1] if len(head) > 1 and head[1].startswith("(") else "")
        key = (tuple(head), text)
        if text and key not in seen:
            seen.add(key)
            rows.append({"url": url, "county": county, "kind": kind, "entity": "|".join(head[1:]), "text": text})
    return rows


def wayne() -> list[dict[str, str]]:
    text = subprocess.run(
        ["pdftotext", "-raw", str(raw.cached(SOURCE, WAYNE_FILE).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    return ballot_issues(text.split("\n"), WAYNE_FILE, "Wayne")


def ocr(url: str) -> list[str]:
    folder = PARSED / "ocr" / "lorain"
    folder.mkdir(parents=True, exist_ok=True)
    if not list(folder.glob("*.png")):
        subprocess.run(
            ["pdftoppm", "-r", "200", "-gray", "-png", str(raw.cached(SOURCE, url).path), str(folder / "p")], check=True
        )
    lines: list[str] = []
    for image in sorted(folder.glob("*.png")):
        out = image.with_suffix(".txt")
        if not out.exists():
            subprocess.run(
                ["tesseract", str(image), str(image.with_suffix("")), "--psm", "4"], check=True, capture_output=True
            )
        lines += out.read_text().split("\n")
    return lines


def lorain() -> list[dict[str, str]]:
    return ballot_issues(ocr(LORAIN_FILE), LORAIN_FILE, "Lorain")


def mf_samples() -> list[dict[str, str]]:
    rows: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for county, path in mf_files():
        if county == "Stark":
            continue
        key = mf_key(county, path)
        text = subprocess.run(
            ["pdftotext", "-raw", str(raw.cached(SOURCE, key).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        for row in ballot_issues(text.split("\n"), key, county):
            rows.setdefault((county, row["kind"], row["entity"], row["text"]), {**row, **mf_meta(county, path)})
    return list(rows.values())


def stark() -> list[dict[str, str]]:
    rows = []
    for county, path in mf_files():
        if county != "Stark":
            continue
        key = mf_key(county, path)
        text = subprocess.run(
            ["pdftotext", "-raw", str(raw.cached(SOURCE, key).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        heading = text.split("Notice is hereby given")[0].replace("LEGAL NOTICE", "")
        entity = re.sub(r"\s+", " ", heading).strip()
        entity = re.sub(r"\s*\(.*\)$", "", entity)
        entity = re.sub(r"(?<=Township) (?:Fire|Road) District(?: Number One)?$", "", entity).strip()
        if entity == "Stark County Library District":
            entity = "Stark County District Library"
        quoted = re.search(r"\u201c(.+?)\u201d", text, flags=re.DOTALL)
        if quoted is None:
            print("no quote:", path.name)
            continue
        body = re.sub(r"\s+", " ", quoted[1]).strip()
        lower = body.lower()
        if "income" in lower:
            kind = "Proposed Income Tax"
        elif lower.startswith("shall bonds"):
            kind = "Proposed Bond Issue"
        else:
            kind = "Proposed Tax Levy"
        for word in ("additional", "renewal"):
            if re.match(rf"(an? )?{word}|.{{0,40}}\b{word}\b", lower):
                kind += f" ({word.title()})"
                break
        rows.append(
            {"url": key, "county": county, "kind": kind, "entity": entity, "text": body, **mf_meta(county, path)}
        )
    return rows


def columns(path: Path) -> list[str]:
    pages = int(
        re.search(
            r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True).stdout
        )[1]
    )
    lines: list[str] = []
    for page in range(1, pages + 1):
        for left in (0, 204, 408):
            lines += subprocess.run(
                [
                    "pdftotext",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-x",
                    str(left),
                    "-y",
                    "0",
                    "-W",
                    "204",
                    "-H",
                    "1116",
                    str(path),
                    "-",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split("\n")
    return lines


def repair(text: str) -> str:
    def fix(match: re.Match[str]) -> str:
        word = match[0]
        return DROPPED.get(word) or {"n": "in", "his": "this", "o": "to"}[word]

    return DROPPED_PATTERN.sub(fix, text)


def montgomery() -> list[dict[str, str]]:
    variants: dict[tuple[str, str], list[list[str]]] = defaultdict(list)
    heads: dict[tuple[str, str], tuple[str, str]] = {}
    with tempfile.TemporaryDirectory() as folder, zipfile.ZipFile(raw.cached(SOURCE, MONTGOMERY_KEY).path) as archive:
        archive.extractall(folder)
        for path in sorted(Path(folder).rglob("*.pdf")):
            for row in ballot_issues(columns(path), MONTGOMERY_KEY, "Montgomery"):
                number = LEAD.match(row["text"])
                if number is None:
                    continue
                text = WATERMARK.sub("", row["text"][number.end() :]).strip()
                key = (number[0].strip(), row["entity"])
                variants[key].append(text.split())
                heads[key] = (row["kind"], row["entity"])
    rows = []
    for key, texts in variants.items():
        size = Counter(len(words) for words in texts).most_common(1)[0][0]
        words = [
            Counter(text[index] for text in texts if len(text) == size).most_common(1)[0][0] for index in range(size)
        ]
        kind, entity = heads[key]
        if entity.startswith("Income Tax|"):
            kind, entity = f"{kind} Income Tax", entity.removeprefix("Income Tax|")
        rows.append(
            {
                "url": MONTGOMERY_KEY,
                "county": "Montgomery",
                "kind": kind,
                "entity": entity,
                "text": repair(" ".join(words)),
                "publisher": "Montgomery County Board of Elections",
                "title": "Sample ballots, November 3, 2026 General Election",
            }
        )
    return rows


def parse() -> None:
    frame = pl.DataFrame(
        hamilton() + summit() + cuyahoga() + wayne() + lorain() + franklin() + mf_samples() + montgomery() + stark(),
        infer_schema_length=None,
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "issues.parquet")
    print(frame.group_by("county").len().rows())


def keys(name: str) -> set[str]:
    bare = re.sub(r"\s+Village$", "", name)
    name = re.sub(
        r"^(City|Village|Township) of (.+)$", lambda m: f"{m[2]} {m[1]}" if m[1] == "Township" else m[2], name
    )
    return {ceda._core(name), ceda._city(name), ceda._core(bare), ceda._city(bare)}


ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def arabic(numeral: str) -> int:
    total = 0
    for index, letter in enumerate(numeral):
        value = ROMAN[letter]
        total += -value if index + 1 < len(numeral) and ROMAN[numeral[index + 1]] > value else value
    return total


def references(text: str) -> str:
    text = " ".join(
        [text, *(f"{a}.{b.zfill(2)}" for a, b in re.findall(r"(?<![\d.-])(\d{1,2})-(\d{1,2})(?![\d-])", text))]
    )
    article = re.search(r"Article ([IVX]+)\b", text)
    if article is None:
        return text
    extra = []
    added = re.search(r"add Section (\d+)\s*\((\w)\)", text)
    if added:
        extra.append(f"Article {article[1]}, Section {added[1]}({added[2]})")
    item = re.search(r"(Item|Sections?) (\d+)(?:\.([A-Z]))?(?: and (\d+))?", text[article.end() :])
    if item:
        number = arabic(article[1])
        width = 1 if item[1] == "Item" else 2
        for part in (item[2], item[4]):
            if part:
                extra.append(f"{number}.{part.zfill(width)}{item[3] or ''}")
    return " ".join([text, *extra])


def token(note: str) -> re.Pattern[str]:
    purpose = note.split("Purpose:")[-1]
    section = re.search(r"Article ([IVX]+), Section ([\d.]*\d)(?:\s*\((\w)\))?", purpose)
    if section:
        tail = rf"\s*\({section[3]}\)" if section[3] else r"(?![\d.]|\s*\()"
        return re.compile(rf"Article {section[1]}, Section {re.escape(section[2])}{tail}", re.IGNORECASE)
    roman = re.search(r"Articles? ([IVX]+)\b", purpose)
    if roman:
        return re.compile(rf"Articles? {roman[1]}\b")
    numbers = re.findall(r"\d+(?:\.\d+)?[a-z]?", purpose)
    return re.compile(rf"(?<![\d.]){re.escape(numbers[-1])}" if numbers else "(?!)")


def pick(
    row: dict[str, str], contests: dict[str, list[tuple[UUID, str, str, str]]], used: set[UUID], fips: str
) -> UUID | None:
    parts = [
        ALIASES.get(part, PRECINCT.sub("", part).replace("CUY ", "CUYAHOGA ")) for part in row["entity"].split("|")
    ]
    options: list[tuple[UUID, str, str, str]] = []
    for size in range(len(parts), 0, -1):
        for start in range(len(parts) - size + 1):
            options += [
                c for k in keys(" ".join(parts[start : start + size])) for c in contests.get(k, []) if c[0] not in used
            ]
        if options:
            break
    options = [c for c in options if c[3] in ("", fips)]
    if row.get("hint"):
        options = [c for c in options if row["hint"] in c[2]]
    kind = row["kind"].upper()
    if any(
        word in kind
        for word in ("CHARTER", "ZONING", "GAS", "ADVISORY", "LOCAL", "LIQUOR", "MERGER", "ORDINANCE", "SPECIAL")
    ):
        wanted = "miscellaneous"
    elif "COMBINATION" in kind:
        wanted = "combination"
    elif "INCOME" in kind:
        wanted = "income tax"
    elif "BOND" in kind:
        wanted = "bond"
    else:
        wanted = "tax levy"
    narrowed = [c for c in options if c[2].lower().startswith(wanted)]
    for word in ("additional", "renewal", "replacement", "decrease", "increase"):
        if len(narrowed) > 1 and word in kind.lower():
            narrowed = [c for c in narrowed if word in c[2].lower()] or narrowed
    text = row["text"].lower()
    if len(narrowed) > 1 and "liquor" in kind.lower():
        sunday = "sunday" in kind.lower()
        narrowed = [c for c in narrowed if ("sunday" in c[2].lower().split("purpose:")[0]) == sunday] or narrowed
    if len(narrowed) > 1:
        narrowed = [
            c
            for c in narrowed
            if all(
                re.search(rf"(?<![\d.]){re.escape(m)} mills?\b", text)
                for m in re.findall(r"([\d.]*\d) mills?\b", c[2].lower())[:1]
            )
        ] or narrowed
    if len(narrowed) > 1:
        narrowed = [c for c in narrowed if token(c[2]).search(references(row["text"]))] or narrowed
    if len(narrowed) > 1:
        words = set(re.findall(r"[a-z]{5,}", f"{row['entity']} {row['text']}".lower()))
        scores = sorted(
            ((len(words & set(re.findall(r"[a-z]{5,}", c[2].split("Purpose:")[-1].lower()))), c) for c in narrowed),
            key=lambda pair: -pair[0],
        )
        if scores[0][0] > scores[1][0]:
            narrowed = [scores[0][1]]
    if len({c[0] for c in narrowed}) != 1:
        return None
    note = narrowed[0][2].lower()
    for word in ("additional", "renewal", "replacement"):
        if word in kind.lower() and word not in note:
            return None
    mills = re.findall(r"([\d.]*\d) mills?\b", note)
    if mills and re.search(r"\bmills?\b", text) and not re.search(rf"(?<![\d.]){re.escape(mills[0])} mills?\b", text):
        return None
    return narrowed[0][0]


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "issues.parquet")
    counties = {ceda._core(f"{county} County") for county in [*frame["county"], *COUNTY_OF.values()]}
    with connect() as conn:
        fips = {
            ceda._core(name): code
            for code, name in conn.execute(
                """select c.code, n.text from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
                   where a.scheme_code = 'us.county' and left(c.code, 2) = '39'"""
            ).fetchall()
            if ceda._core(name) in counties
        }
        directory = json.loads(raw.cached(SCHOOLS, DIRECTORY).path.read_text())["results"]
        home = {entry["leaid"]: str(entry["county_code"]) for entry in directory}
        leas = {entry["leaid"]: entry["lea_name"].lower() for entry in directory}
        contests: dict[str, list[tuple[UUID, str, str, str]]] = {}
        for contest, code, name, note in conn.execute(
            """select c.id, split_part(x.value, ':', 3), n.text, coalesce(t.body_md, '') from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value like %s
               join election e on e.id = c.election_id
               join name n on n.entity_id = e.polity_id and n.is_preferred and n.lang = 'en'
               left join note t on t.entity_id = c.id and t.kind = 'context'""",
            [f"{DAY}:OH:%:question:%"],
        ).fetchall():
            code = code.removesuffix("-sd")
            if len(code) == 7 and code in leas and name.split()[0].lower() not in leas[code]:
                home.pop(code, None)
            if len(code) == 10:
                home[code] = code[:5]
            elif len(code) == 5:
                home[code] = code
            if home.get(code, "") not in ("", *fips.values()):
                continue
            for key in keys(name):
                contests.setdefault(key, []).append((contest, name, note, home.get(code, "")))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Official ballot language for Ohio county local issues on the November 3, 2026 ballot",
            polity="US-OH",
        )
        sources: dict[str, UUID] = {}
        covered = {
            contest
            for (contest,) in conn.execute("select contest_id from ballot_question_text where source_id is not null")
        }
        for folder in DATA.glob("changesets/*"):
            table = folder / "ballot_question_text.parquet"
            if table.exists() and json.loads((folder / "manifest.json").read_text())["pipeline"] == SOURCE:
                covered |= {UUID(str(contest)) for contest in pl.read_parquet(table)["contest_id"]}
        kept = 0
        used: set[UUID] = set()
        pending = list(frame.iter_rows(named=True))
        progress = True
        while pending and progress:
            progress = False
            deferred = []
            for row in pending:
                county = COUNTY_OF.get(row["entity"], row["county"])
                contest = pick(row, contests, used, fips[ceda._core(f"{county} County")])
                if contest is None:
                    deferred.append(row)
                    continue
                progress = True
                used.add(contest)
                if contest in covered:
                    continue
                kept += 1
                if row["url"] not in sources:
                    _, _, publisher, title = SOURCES.get(row["url"]) or (
                        "",
                        None,
                        row.get("publisher") or "Franklin County Board of Elections",
                        row.get("title")
                        or f"Notice of election: {re.sub(r'-+', ' ', urlparse(row['url']).path.rsplit('/', 1)[-1].removeprefix('Notice-of-Election-'))}, November 3, 2026 General Election",
                    )
                    sources[row["url"]] = cs.source(publisher, title)
                    cs.add(
                        "source",
                        id=sources[row["url"]],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url=row["url"],
                    )
                    cs.artifact(raw.cached(SOURCE, row["url"]), sources[row["url"]])
                cs.add("citation", entity_id=contest, source_id=sources[row["url"]])
                cs.add(
                    "ballot_question_text",
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=row["text"],
                    url=row["url"],
                    source_id=sources[row["url"]],
                )
            pending = deferred
        for row in pending:
            print("no match:", row.get("county"), row["entity"], row["kind"])
        print(f"{kept} of {frame.height} issues")
        return cs.write()
