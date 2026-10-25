import csv
import html
import re
import subprocess
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.houston_citysec"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
SCANS = Path(__file__).with_name("houston_citysec_scans.tsv")
PUBLISHER = "City of Houston, Office of the City Secretary"
TITLE = "Canvass of general and runoff elections"
LISTING = "https://www.houstontx.gov/citysec/elections/electionsindex.html"
FILE = "https://www.houstontx.gov/citysec/elections/{name}.pdf"
FILES = {
    "110591": (date(1991, 11, 5), "scan"),
    "120791": (date(1991, 12, 7), "scan"),
    "110293": (date(1993, 11, 2), "scan"),
    "120493": (date(1993, 12, 4), "scan"),
    "110795": (date(1995, 11, 7), "scan"),
    "120995": (date(1995, 12, 9), "scan"),
    "110497": (date(1997, 11, 4), "scan"),
    "120697": (date(1997, 12, 6), "scan"),
    "110299": (date(1999, 11, 2), "scan"),
    "120499": (date(1999, 12, 4), "scan"),
    "110601": (date(2001, 11, 6), "scan"),
    "120101": (date(2001, 12, 1), "scan"),
    "110403": (date(2003, 11, 4), "scan"),
    "120603": (date(2003, 12, 6), "scan"),
    "110805": (date(2005, 11, 8), "scan"),
    "121005": (date(2005, 12, 10), "scan"),
    "110607": (date(2007, 11, 6), "scan"),
    "120807": (date(2007, 12, 8), "scan"),
    "110513": (date(2013, 11, 5), "scan"),
    "110309": (date(2009, 11, 3), "summary"),
    "110811": (date(2011, 11, 8), "summary"),
    "121011": (date(2011, 12, 10), "summary"),
    "110519": (date(2019, 11, 5), "summary"),
    "110723": (date(2023, 11, 7), "summary"),
    "120923": (date(2023, 12, 9), "canvass"),
}
RUNOFFS = {
    2009: date(2009, 12, 12),
    2013: date(2013, 12, 14),
    2011: date(2011, 12, 10),
    2019: date(2019, 12, 14),
    2023: date(2023, 12, 9),
}
PLACE = "4835000"
OFFICES = ("mayor", "controller", "district", "at-large")
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
LABEL = re.compile(
    r"^(Cast Votes|Undervotes|Overvotes|Ballots? Cast.*|Ballot by Mail.*|Early Voting.*|Election Day.*|.*Provisional.*"
    r"|Registered.*|Turnout.*|Total.*|Voter Turnout.*|Precinct)$",
    re.IGNORECASE,
)
SKIP = re.compile(
    r"cast votes|undervotes?|overvotes?|under votes|over votes|^total|registered|ballots|precincts|turnout|choice|party",
    re.IGNORECASE,
)
METHOD = (
    "City totals from the City Secretary's canvass, summed over Harris, Fort Bend, and Montgomery counties where the "
    "canvass reports each county. A seat no candidate won with a majority went to a runoff between the top two."
)
SCANNED = (
    "City totals from the City Secretary's combined summary for Harris, Fort Bend, and Montgomery counties, a scanned "
    "page. Each count was read by two independent readers and checked against the percentage printed beside it. A "
    "seat no candidate won with a majority went to a runoff between the top two."
)

type Word = tuple[float, float, float, float, str]


def office_of(compact: str) -> str | None:
    if re.fullmatch(r"(CITYOFHOUSTON)?MAYOR(CITYOFHOUSTON)?(VOTEFOR.*)?", compact):
        return "mayor"
    if re.fullmatch(r"(CITYOFHOUSTON)?(CITY)?CONTROLLER(CITYOFHOUSTON)?(VOTEFOR.*)?", compact):
        return "controller"
    if re.search(
        r"PROPOSITION|CHARTER|ISD|SCHOOL|COUNTY|STATE|JUDGE|TRUSTEE|COLLEGE|SHERIFF|CONSTABLE|JUSTICE|REPRESENTATIVE"
        r"|SENAT|CONGRESS|AMENDMENT|MEASURE|BOND",
        compact,
    ):
        return "other"
    if not re.search(r"MEMBER|TRICT|LARGE|COUNCIL", compact):
        return "other" if "VOTEFOR" in compact else None
    tail = re.sub(r"(CITYOFHOUSTON|UNEXPIREDTERM|VOTEFOR.*)+$", "", compact)
    if match := re.search(r"LARGE(?:POSITION|POS\w*)?(?:NO)?([1-5])$", tail):
        return f"at-large-{match[1]}"
    if match := re.search(r"DISTRICT([A-K])$", tail):
        return f"district-{match[1].lower()}"
    return "unknown"


def county(text: str) -> str:
    return next((name for name in ("Fort Bend", "Montgomery", "Harris") if name in text), "Harris")


def summary(text: str) -> list[tuple[str, str, str, int, float | None]]:
    pages = text.split("\f")
    stop = re.compile(
        r"(?m)^.*(?:Cumulative|Canvass|Total Number of Voters|Accumulated Totals|PRECINCT|Precinct|Harris County\s+Fort Bend).*$"
    )
    blocks: list[tuple[str, str]] = []
    inside = False
    for page in pages:
        inside = inside or "COUNTIES COMBINED" in page.upper()
        if inside:
            head = stop.split(page)[0]
            blocks.append(("Combined", head))
            if len(head) < len(page) and "COUNTIES COMBINED" not in page[len(head) :].upper():
                inside = False
    combined = bool(blocks)
    if not combined:
        blocks = [
            (county(page[:3000]), page)
            for page in pages
            if re.search(r"Cumulative|Accumulated Totals", page) and "Canvass" not in page[:400]
        ]
    rows = []
    office = None
    seen: set[str] = set()
    for where, block in blocks:
        for line in block.split("\n"):
            stripped = re.sub(r"\s+", " ", line).strip()
            if not stripped:
                continue
            tokens = re.split(r"\s{2,}", line.strip())
            if (
                len(tokens) >= 3
                and re.search("[A-Za-z]{2}", tokens[0])
                and re.fullmatch(r"[\d,]+", tokens[-2])
                and not re.search("[A-Za-z]", tokens[-1])
            ):
                if office not in (None, "other") and not SKIP.search(tokens[0]):
                    cleaned = tokens[-1].rstrip("%").replace(" ", "")
                    pct = float(cleaned) if re.fullmatch(r"\d+(\.\d+)?", cleaned) else None
                    rows.append((office, where, tokens[0], int(tokens[-2].replace(",", "")), pct))
                continue
            compact = re.sub(r"\s+(COUNT|PERCENT|TOTAL|VOTE %)\b", "", stripped.upper())
            compact = re.sub(r"[^A-Z0-9]", "", compact)
            if re.fullmatch(
                r"(BALLOTS|COUNT|PERCENT|CHOICE|PARTY|PAGE|RUNTIME|RUNDATE|VOTEFOR\d*$).*", compact
            ) or not re.search("[A-Z]{3}", compact):
                continue
            found = office_of(compact)
            if found is not None:
                if combined and found in seen and found != office and found != "other":
                    return rows
                office = found
                seen.add(found)
    return rows


def words(pdf: Path) -> list[list[Word]]:
    out = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], check=True, capture_output=True, text=True).stdout
    return [
        [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(chunk)]
        for chunk in out.split("<page ")[1:]
    ]


def lines(page: list[Word]) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for word in sorted(page, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        if rows and abs((rows[-1][0][1] + rows[-1][0][3]) / 2 - (word[1] + word[3]) / 2) < 2.5:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row) for row in rows]


def chunks(row: list[Word], gap: float = 6.0) -> list[Word]:
    out: list[Word] = []
    for word in row:
        if out and word[0] - out[-1][2] < gap:
            first = out[-1]
            out[-1] = (first[0], min(first[1], word[1]), word[2], max(first[3], word[3]), first[4] + " " + word[4])
        else:
            out.append(word)
    return out


def canvass(pdf: Path) -> list[tuple[str, str, list[str] | None, list[int]]]:
    results: list[tuple[str, str, list[str] | None, list[int]]] = []
    regions: list[tuple[str | None, list[str]]] = []
    bend: list[str] = []
    numbers: list[int] = []
    for page in words(pdf):
        rows = lines(page)
        text = " ".join(w[4] for w in page[:200])
        if "Bend" in text:
            for row in rows:
                joined = " ".join(w[4] for w in row)
                if re.match(r"(Mayor|City Controller|Controller|Council Member)", joined):
                    for piece in re.findall(r".*?City of Houston", joined) or [joined]:
                        found = office_of(re.sub(r"[^A-Z0-9]", "", piece.upper()))
                        found = found if found and found.startswith(OFFICES) else "other"
                        if found not in bend or (found == "other" and bend[-1] != "other"):
                            bend.append(found)
                    break
            for row in rows:
                if row[0][4] == "Totals" and bend:
                    numbers.extend(int(w[4].replace(",", "")) for w in row[1:] if re.fullmatch(r"[\d,]+", w[4]))
            continue
        heads = []
        for index, row in enumerate(rows):
            for chunk in chunks(row, 12.0):
                if re.search(r"Houston|Vote for", chunk[4], re.IGNORECASE):
                    found = office_of(re.sub(r"[^A-Z0-9]", "", chunk[4].upper()))
                    heads.append((found if found and found.startswith(OFFICES) else None, index))
        if heads:
            top = max(index for _, index in heads)
            start = next(
                (i for i in range(top + 1, len(rows)) if re.match(r"(Precinct|Totals|\d|[A-Z]{2}$)", rows[i][0][4])),
                len(rows),
            )
            found: list[tuple[float, float, list[Word]]] = []
            for row in rows[top + 1 : start]:
                for chunk in chunks(row):
                    if LABEL.match(chunk[4]) or not re.search("[A-Za-z]", chunk[4]):
                        continue
                    merged = next((n for n, f in enumerate(found) if f[0] < chunk[2] and chunk[0] < f[1]), None)
                    if merged is None:
                        found.append((chunk[0], chunk[2], [chunk]))
                    else:
                        left, right, parts = found[merged]
                        found[merged] = (min(left, chunk[0]), max(right, chunk[2]), [*parts, chunk])
            names = []
            for _, _, parts in sorted(found, key=lambda f: (f[0] + f[1]) / 2):
                if all(w[3] - w[1] > w[2] - w[0] for w in parts):
                    parts = sorted(parts, key=lambda w: -w[1])
                else:
                    parts = sorted(parts, key=lambda w: (round(w[1] / 6), w[0]))
                names.append(" ".join(w[4] for w in parts))
            regions = [(next((office for office, _ in heads if office), None), names)]
        for row in rows:
            if row[0][4] != "Totals":
                continue
            for office, names in regions:
                if office is None:
                    continue
                values = [int(w[4].replace(",", "")) for w in row[1:] if re.fullmatch(r"[\d,]+", w[4])]
                k = next((k for k in range(1, len(values)) if values[k] == sum(values[:k]) and k <= len(names)), None)
                if k is None and len(values) == len(names):
                    k = len(names)
                if k is None:
                    raise ValueError(f"{pdf.name}: {office} totals do not tie")
                results.append((office, county(text), names[:k], values[:k]))
    for office in bend:
        for k in range(1, len(numbers)):
            head = numbers[:k]
            if k + 3 < len(numbers) and numbers[k] == sum(head) and numbers[k + 3] == sum(numbers[k : k + 3]):
                if office != "other":
                    results.append((office, "Fort Bend", None, head))
                numbers = numbers[k + 4 :]
                break
        else:
            raise ValueError(f"{pdf.name}: Fort Bend {office} totals do not tie")
    return results


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for name in FILES:
        raw.fetch(SOURCE, FILE.format(name=name), suffix=".pdf")


def parse() -> None:
    rows = []
    with SCANS.open(newline="") as handle:
        scanned = list(csv.DictReader(handle, delimiter="\t"))
    for name, (day, kind) in FILES.items():
        url = FILE.format(name=name)
        path = raw.cached(SOURCE, url).path
        if kind == "scan":
            groups: dict[str, list[dict[str, str]]] = {}
            for row in scanned:
                if row["file"] == name:
                    groups.setdefault(row["office"], []).append(row)
            if not groups:
                raise ValueError(f"{name}: no transcription")
            for office, group in groups.items():
                total = sum(int(row["votes"]) for row in group if row["check"] != "unprinted")
                for row in group:
                    if row["check"] == "" and abs(100 * int(row["votes"]) / total - float(row["percent"])) > 0.011:
                        raise ValueError(f"{name}: {office} {row['candidate']} does not tie to its percentage")
                    rows.append(
                        {
                            "day": day,
                            "url": url,
                            "office": office,
                            "county": "Combined",
                            "candidate": row["candidate"],
                            "votes": int(row["votes"]),
                            "misprint": row["percent"] if row["check"] == "misprint" else None,
                            "page": int(row["page"]),
                            "box": [float(row[edge]) for edge in ("x0", "y0", "x1", "y1")],
                        }
                    )
            continue
        if kind == "canvass":
            for office, where, names, values in canvass(path):
                if names is None:
                    order = [
                        r["candidate"]
                        for r in rows
                        if r["url"] == url and r["office"] == office and r["county"] == "Harris"
                    ]
                    if len(order) != len(values):
                        raise ValueError(f"{name}: Fort Bend {office} has {len(values)} columns for {len(order)} names")
                    names = order
                for candidate, votes in zip(names, values, strict=True):
                    rows.append(
                        {
                            "day": day,
                            "url": url,
                            "office": office,
                            "county": where,
                            "candidate": candidate,
                            "votes": votes,
                        }
                    )
            continue
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
        ).stdout
        parsed = summary(text)
        groups: dict[tuple[str, str], list[tuple[int, float | None]]] = {}
        for office, where, _, votes, pct in parsed:
            groups.setdefault((office, where), []).append((votes, pct))
        bad = set()
        for (office, where), values in groups.items():
            total = sum(v for v, _ in values) or 1
            for votes, pct in values:
                if pct is not None and min(abs(100 * votes / total - pct), abs(100 * votes / total - pct / 100)) > 0.6:
                    bad.add(office)
            if office == "unknown":
                bad.add(office)
        if bad:
            print(f"{name}: skipped {sorted(bad)}, the printed percentages do not match the counts")
        for office, where, candidate, votes, _ in parsed:
            if office in bad or (name == "121011" and not office.startswith("district")):
                continue
            rows.append(
                {"day": day, "url": url, "office": office, "county": where, "candidate": candidate, "votes": votes}
            )
    frame = pl.DataFrame(
        [{"misprint": None, "page": None, "box": None, **row} for row in rows],
        schema_overrides={"misprint": pl.Utf8, "page": pl.Int32, "box": pl.List(pl.Float64)},
        infer_schema_length=None,
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")
    print(frame.group_by("day").agg(pl.col("office").n_unique().alias("contests"), pl.len()).sort("day"))


def display(text: str) -> str:
    text = re.sub(r"\s*\((W|WRITE[- ]IN)\)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text.replace("“", '"').replace("”", '"').replace(" .", ".")).strip()
    if re.sub("Mc", "", text) != re.sub("Mc", "", text).upper():
        return text
    text = re.sub(
        r"[^\W\d_]+", lambda found: found[0] if found[0] in ("II", "III", "IV") else found[0].capitalize(), text
    )
    return re.sub(r"\bMc([a-z])", lambda found: "Mc" + found[1].upper(), text)


def fold(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"[\"'“”](\w+)[\"'“”]", " ", plain)
    words = [word for word in re.findall(r"[a-z]+", plain) if word not in ("jr", "sr", "ii", "iii", "iv", "w")]
    return words or [plain]


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Houston mayor, controller, and City Council 1991-2023 from the City Secretary's canvasses, on their "
                "election days with runoffs chained to their first rounds"
            ),
            polity="US-TX",
        )

        def name(entity: UUID, text: str, reason: str = "Renamed to match the City Secretary's canvass") -> None:
            found = conn.execute("select id, text from name where entity_id = %s and is_preferred", [entity]).fetchone()
            if found is not None and found[1] == text:
                return
            if found is not None:
                cs.add("name_drop", name_id=found[0], entity_id=entity, text=found[1], reason=reason)
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LISTING,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, LISTING), source)
        artifacts = {url: cs.artifact(raw.cached(SOURCE, url), source) for url in sorted(set(frame["url"]))}
        pages: dict[tuple[str, int], UUID] = {}
        for url, number in (
            frame.filter(pl.col("page").is_not_null()).select("url", "page").unique().sort("url", "page").iter_rows()
        ):
            artifact = raw.cached(SOURCE, url)
            page = pages[(url, number)] = cs.page(artifacts[url], number)
            image = PAGES / f"{artifact.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(artifact.path), str(image.with_suffix(""))],
                    check=True,
                )
            with Image.open(image) as opened:
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifacts[url],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=opened.width,
                    height=opened.height,
                )
        system = cs.key("us.electoral_system", "two_round")
        if system in cs.minted:
            raise ValueError("the two-round system must already exist")
        found = conn.execute(
            "select t.polity_id from area_code c join polity_territory t on t.area_id = c.area_id where c.code = %s",
            [PLACE],
        ).fetchone()
        if found is None:
            raise ValueError("no polity for Houston")
        polity = found[0]
        current = dict(conn.execute("select entity_id, path from slug where is_current").fetchall())
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/tx/%%'").fetchall()}
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "completeness")
        people: dict[tuple[str, str], UUID] = {}
        preferred: dict[UUID, str] = {}
        for person, ballot, text in conn.execute(
            """select cy.person_id, coalesce(cy.ballot_name, o.ballot_label), n.text
               from candidacy cy join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id join election e on e.id = k.election_id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where e.polity_id = %s order by e.date_from""",
            [polity],
        ).fetchall():
            words = fold(ballot)
            people[(words[0], words[-1])] = person
            preferred[person] = text
        renamed: set[UUID] = set()
        seats: set[UUID] = set()
        minted: set[str] = set()
        elections: dict[date, tuple[UUID, str]] = {}
        firsts: dict[tuple[int, str], UUID] = {}
        values: list[tuple[str, str, str, int, int]] = []
        locators: list[tuple[str, str, str, str, float, float, float, float]] = []
        report: dict[str, int] = {"taken over": 0, "new": 0, "runoffs chained": 0, "unmatched options": 0}
        differences: list[str] = []

        def place(entity: UUID, path: str) -> str:
            if current.get(entity) == path:
                return path
            base, n = path, 1
            while path in taken:
                n += 1
                path = f"{base}-{n}"
            taken.add(path)
            cs.add("slug", path=path, entity_id=entity)
            current[entity] = path
            return path

        def election(day: date) -> tuple[UUID, str]:
            if day in elections:
                return elections[day]
            kind = "runoff" if day.month == 12 else "local"
            parent = cs.key("us.election", f"{day}:TX")
            if parent in cs.minted:
                cs.add(
                    "election",
                    id=parent,
                    polity_id=cs.polity("US-TX"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} Texas {kind} elections")
                place(parent, f"us/tx/{day}-{kind}")
            found = conn.execute(
                "select entity_id from external_id where scheme = 'us.election' and value = %s",
                [f"{day:%Y-%m}:TX:{PLACE}"],
            ).fetchone()
            entity = found[0] if found else cs.key("us.election", f"{day}:TX:{PLACE}")
            cs.add(
                "election",
                id=entity,
                polity_id=polity,
                parent_id=parent,
                kind=kind,
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            name(entity, f"{day.year} Houston {kind} election")
            elections[day] = (entity, place(entity, f"us/tx/houston/{day}-{kind}"))
            return elections[day]

        def seat(office: str) -> tuple[str, str, str]:
            if office == "mayor":
                return "Mayor_mayor_1", "mayoral", "mayor"
            if office == "controller":
                return "", "city controller", "controller"
            label = office.removeprefix("district-") if office.startswith("district") else f"at-large {office[-1]}"
            return f"City Council_{label}_1", "City Council", f"council-{office}"

        def owner(office: str) -> UUID:
            key = "council" if office.startswith(("district", "at-large")) else office
            entity = cs.key("us.body" if key == "council" else "us.office", f"TX:{PLACE}:{key}")
            if entity in cs.minted and entity not in renamed:
                renamed.add(entity)
                cs.add("office", id=entity, polity_id=polity, kind="other")
                name(entity, f"Houston City {key.title()}")
            return entity

        def contest_row(contest: UUID, office: str, election_id: UUID, runoff: bool, previous: UUID | None) -> None:
            council = office.startswith(("district", "at-large"))
            post = None
            if office.startswith("at-large"):
                body = owner(office)
                post = cs.key("us.seat", f"{body}:At-Large {office[-1]}")
                if post in cs.minted and post not in seats:
                    seats.add(post)
                    cs.add(
                        "seat",
                        id=post,
                        body_id=body,
                        district_area_id=None,
                        apportioned_area_id=cs.area("us.place", PLACE),
                        label=f"At-Large {office[-1]}",
                    )
            cs.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="body_seats" if council else "office",
                office_id=None if council else owner(office),
                body_id=owner(office) if council else None,
                district_area_id=cs.area("us.local_district", f"48:{PLACE}:council:{office}")
                if office.startswith("district")
                else cs.area("us.place", PLACE),
                seat_id=post,
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=previous,
                electoral_system_id=system,
                default_result_kind="counted",
                term="full",
            )

        def title(day: date, office: str, runoff: bool) -> str:
            _, words, _ = seat(office)
            district = (
                f", District {office[-1].upper()}"
                if office.startswith("district")
                else f", At-Large {office[-1]}"
                if office.startswith("at-large")
                else ""
            )
            return f"{day.year} Houston {words} {'runoff' if runoff else 'election'}{district}"

        def lookup(values: list[str | None]) -> tuple[UUID, str] | None:
            return conn.execute(
                "select entity_id, value from external_id where scheme = 'us.contest' and value = any(%s)",
                [[value for value in values if value]],
            ).fetchone()

        for (day, office), rows in frame.sort("day", "office").group_by("day", "office", maintain_order=True):
            runoff = day.month == 12
            election_id, path = election(day)
            key, _, leaf = seat(office)
            month = f"alged:{PLACE}_{day.year}_{day.month}_houston_TX_{key}" if key else None
            wiki = f"wikipedia:{day}:TX:{PLACE}:mayor" if office == "mayor" else None
            found = lookup([month, wiki])
            contest_key = found[1] if found else f"houston_citysec:{day}:{office}"
            contest = cs.key("us.contest", contest_key)
            report["taken over" if found else "new"] += 1
            previous = firsts.get((day.year, office)) if runoff else None
            if not runoff:
                firsts[(day.year, office)] = contest
            contest_row(contest, office, election_id, runoff, previous)
            name(contest, title(day, office, runoff))
            place(contest, f"{path}/{leaf}")
            prior = conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchone()
            replaced = None
            if prior is not None and prior[0] == cs.key(f"{SOURCE}.result_set", contest_key):
                replaced = conn.execute("select supersedes_id from result_set where id = %s", [prior[0]]).fetchone()[0]
                prior = None
            if prior is not None:
                kept = dict(zip(columns, prior, strict=True))
                kept["election_id"] = election_id
                cs.add("result_set", **kept, is_default=False, status="superseded")
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
                supersedes_id=prior[0] if prior else replaced,
            )
            scan = FILES[Path(rows["url"][0]).stem][1] == "scan"
            cs.add("note", entity_id=result_set, kind="methodology", body_md=SCANNED if scan else METHOD)
            for candidate, votes, printed in (
                rows.filter(pl.col("misprint").is_not_null()).select("candidate", "votes", "misprint").iter_rows()
            ):
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        f"The canvass prints {votes:,} votes for {display(candidate)} beside {printed} percent, "
                        "which does not match that count; the count is stored as printed."
                    ),
                )
            options: list[tuple[list[str], UUID, UUID | None, UUID | None, str]] = []
            for option, label, person, candidacy in conn.execute(
                """select o.id, o.ballot_label, cy.person_id, cy.id from contest_option o
                   left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   where o.contest_id = %s and o.kind = 'candidate'""",
                [contest],
            ).fetchall():
                options.append((fold(label), option, person, candidacy, label))
            before = (
                {
                    label: votes
                    for label, votes in conn.execute(
                        """select o.ballot_label, sum(v.value)::int from result_value v
                       join result_set r on r.k = v.result_set_k join contest_option o on o.k = v.option_k
                       where r.id = %s group by 1""",
                        [prior[0]],
                    ).fetchall()
                }
                if prior
                else {}
            )
            totals: dict[tuple[str, ...], list] = {}
            for candidate, votes, number, box in rows.select("candidate", "votes", "page", "box").iter_rows():
                write_in = bool(re.search(r"\((W|WRITE[- ]IN)\)", candidate, re.IGNORECASE))
                group = ("write-in",) if write_in else tuple(fold(display(candidate)))
                entry = totals.setdefault(group, [display(candidate), 0, write_in, []])
                entry[1] += votes
                entry[3].append((number, box))
            ranked: list[tuple[int, UUID]] = []
            chosen: dict[tuple[str, ...], UUID] = {}
            for group, (label, votes, write_in, _) in sorted(totals.items(), key=lambda item: -item[1][1]):
                if write_in:
                    option = cs.key("us.option", f"{contest_key}:write_in")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    chosen[group] = option
                    continue
                words = list(group)
                match = (
                    next((entry for entry in options if entry[0][-1] == words[-1]), None)
                    or next(
                        (
                            entry
                            for entry in options
                            if entry[0][0] == words[0]
                            and (
                                set(entry[0][1:]) & set(words[1:])
                                or SequenceMatcher(None, entry[0][-1], words[-1]).ratio() >= 0.8
                            )
                        ),
                        None,
                    )
                    or next((entry for entry in options if set(entry[0]) == set(words)), None)
                )
                option, person, candidacy = (None, None, None) if match is None else match[1:4]
                if match is not None:
                    options.remove(match)
                    if before and abs(before.get(match[4], 0) - votes) > max(1, votes // 100):
                        differences.append(
                            f"{title(day, office, runoff)}: {label} {votes:,} (ALGED {before.get(match[4], 0):,})"
                        )
                person = person or people.get((words[0], words[-1]))
                if person is None:
                    person = cs.key(f"{SOURCE}.person", f"{words[0]}:{words[-1]}")
                    people[(words[0], words[-1])] = person
                    if str(person) not in minted:
                        minted.add(str(person))
                        cs.add("person", id=person)
                        name(person, label)
                elif person not in renamed and set(fold(preferred.get(person, label))) < set(words):
                    renamed.add(person)
                    name(person, label, "The City Secretary's canvass gives the full name")
                option = option or cs.key("us.option", f"{contest_key}:{slug(label)}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=candidacy or cs.key("us.candidacy", f"{contest_key}:{slug(label)}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=label,
                )
                chosen[group] = option
                ranked.append((votes, option))
            report["unmatched options"] += len(options)
            differences += [f"{title(day, office, runoff)}: unmatched {entry[4]}" for entry in options]
            total = sum(entry[1] for entry in totals.values())
            decided = runoff or ranked[0][0] * 2 > total
            for rank, (_, option) in enumerate(ranked):
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=("elected" if decided else "advanced") if rank < (1 if decided else 2) else "lost",
                )
            unit = (
                cs.unit("us.local_district", f"48:{PLACE}:council:{office}")
                if office.startswith("district")
                else cs.unit("us.place", PLACE)
            )
            if unit in cs.minted:
                raise ValueError(f"no reporting unit for {office}")
            for group, (_, votes, _, located) in totals.items():
                found = located[0] if len(located) == 1 and located[0][0] is not None else None
                values.append((str(result_set), str(unit), str(chosen[group]), votes, 8 if found else 0))
                if found:
                    page_id = pages[(rows["url"][0], found[0])]
                    locators.append((str(result_set), str(unit), str(chosen[group]), str(page_id), *found[1]))
        for year, day in RUNOFFS.items():
            election_id, path = election(day)
            for office in sorted({office for (y, office) in firsts if y == year}):
                if (day, office) in set(frame.select("day", "office").unique().iter_rows()):
                    continue
                key, _, leaf = seat(office)
                found = lookup([f"alged:{PLACE}_{year}_12_houston_TX_{key}" if key else None])
                if found is None:
                    continue
                contest = found[0]
                report["runoffs chained"] += 1
                contest_row(contest, office, election_id, True, firsts[(year, office)])
                name(contest, title(day, office, True))
                place(contest, f"{path}/{leaf}")
                for row in conn.execute(
                    f"select {', '.join(columns)}, is_default, status, supersedes_id from result_set where contest_id = %s",
                    [contest],
                ).fetchall():
                    kept = dict(zip((*columns, "is_default", "status", "supersedes_id"), row, strict=True))
                    if kept["election_id"] != election_id:
                        kept["election_id"] = election_id
                        cs.add("result_set", **kept)
        print(report)
        for line in differences:
            print("  differs:", line)
        cs.frame(
            "result_value",
            pl.DataFrame(
                values, schema=["result_set_id", "unit_id", "option_id", "value", "flags"], orient="row"
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.col("flags").cast(pl.Int16),
            ),
        )
        if locators:
            cs.frame(
                "value_locator",
                pl.DataFrame(
                    locators,
                    schema=["result_set_id", "unit_id", "option_id", "page_id", "x0", "y0", "x1", "y1"],
                    orient="row",
                ).with_columns(
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    source_id=pl.lit(None, pl.Utf8),
                ),
            )
        return cs.write()
