import json
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
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
CUYAHOGA = "https://boe.cuyahogacounty.gov/"
CUYAHOGA_FILE = INBOX / "cuyahoga" / "PDF Sample Ballot Package.pdf"
CUYAHOGA_BOXES = ((5, 20, 195, 375), (5, 215, 175, 355), (5, 392, 215, 325), (6, 20, 195, 545), (6, 215, 190, 345))
WAYNE_FILE = "https://www.waynecountyoh.gov/wp-content/uploads/2026/09/Sample-Ballots-ALL-WATERMARK-09192026-1352.pdf"
LORAIN_FILE = "https://www.voteloraincountyohio.gov/_files/ugd/2568d0_fd0d57cfa16c443899cf8c9653a28945.pdf"
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
        CUYAHOGA_FILE,
        "Cuyahoga County Board of Elections",
        "Sample ballot (Bay Village 01-A), November 3, 2026 General Election",
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
STOP = re.compile(r"^(YES|NO|SI|FOR\b.*|AGAINST\b.*|A FAVOR\b.*|EN CONTRA\b.*)$")
ALIASES = {"Stark County District Library": "Stark County"}
COUNTY_OF = {"Stark County District Library": "Stark"}
SCHOOLS = "us.oh_questions_2026"
DIRECTORY = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory/2022/?fips=39"
PRECINCT = re.compile(r"(?<=[a-z])\s+(?:\d+-?[A-Z]|[A-Z])$|(?<=[A-Z])\s+\d+-[A-Z]$")
START = re.compile(
    r"^(?:\d+(?:\s\d)?\s+)?(?:T)?(Proposed|PROPOSED|Special Election|SPECIAL ELECTION|Advisory|ADVISORY|Local|LOCAL)\b"
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
    rows = []
    path = str(raw.cached(SOURCE, CUYAHOGA).path)
    for page, x, width, bottom in CUYAHOGA_BOXES:
        text = subprocess.run(
            [
                "pdftotext",
                "-f",
                str(page),
                "-l",
                str(page),
                "-layout",
                "-x",
                str(x),
                "-y",
                "135",
                "-W",
                str(width),
                "-H",
                str(bottom - 135),
                path,
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        flat = re.sub(r"\s+", " ", text).strip()
        head, _, body = flat.partition("A majority affirmative vote is necessary for passage.")
        kind = re.sub(r"^\d+ ", "", head)
        rows.append(
            {
                "url": CUYAHOGA,
                "county": "Cuyahoga",
                "kind": kind.split(" County of")[0].split(" Board of")[0].split(" Health and")[0],
                "entity": "Cuyahoga County",
                "text": body.strip(),
            }
        )
    return rows


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
            r"^(?:\d+(?:\s\d)?\s+)?T?(?=Proposed|PROPOSED|Special|SPECIAL|Advisory|ADVISORY|Local|LOCAL)", "", head[0]
        )
        cursor = index + 1
        if lines[cursor].startswith("necessary for passage"):
            cursor += 1
        body = []
        while cursor < len(lines) and not STOP.match(lines[cursor]):
            body.append(lines[cursor])
            cursor += 1
        text = re.sub(r"\s+", " ", " ".join(body)).strip()
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


def parse() -> None:
    frame = pl.DataFrame(hamilton() + summit() + cuyahoga() + wayne() + lorain())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "issues.parquet")
    print(frame.group_by("county").len().rows())


def keys(name: str) -> set[str]:
    bare = re.sub(r"\s+Village$", "", name)
    name = re.sub(
        r"^(City|Village|Township) of (.+)$", lambda m: f"{m[2]} {m[1]}" if m[1] == "Township" else m[2], name
    )
    return {ceda._core(name), ceda._city(name), ceda._core(bare), ceda._city(bare)}


def token(note: str) -> re.Pattern[str]:
    purpose = note.split("Purpose:")[-1]
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
    kind = row["kind"].upper()
    if any(
        word in kind
        for word in ("CHARTER", "ZONING", "GAS", "ADVISORY", "LOCAL", "LIQUOR", "MERGER", "ORDINANCE", "SPECIAL")
    ):
        wanted = "miscellaneous"
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
        narrowed = [c for c in narrowed if token(c[2]).search(row["text"])] or narrowed
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
        home = {
            entry["leaid"]: str(entry["county_code"])
            for entry in json.loads(raw.cached(SCHOOLS, DIRECTORY).path.read_text())["results"]
        }
        contests: dict[str, list[tuple[UUID, str, str, str]]] = {}
        for contest, code, name, note in conn.execute(
            """select c.id, split_part(x.value, ':', 3), n.text, coalesce(t.body_md, '') from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value like %s
               join election e on e.id = c.election_id
               join name n on n.entity_id = e.polity_id and n.is_preferred and n.lang = 'en'
               left join note t on t.entity_id = c.id and t.kind = 'context'""",
            [f"{DAY}:OH:%:question:%"],
        ).fetchall():
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
                    _, _, publisher, title = SOURCES[row["url"]]
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
            print("no match:", row["entity"], row["kind"])
        print(f"{kept} of {frame.height} issues")
        return cs.write()
