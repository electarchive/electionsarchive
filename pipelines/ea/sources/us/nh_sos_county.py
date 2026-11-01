import html
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nh_sos_county"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = Path(__file__).with_name("nh_sos_county_files.tsv")
WAYBACK = (
    "https://web.archive.org/web/2026id_/https://www.sos.nh.gov/sites/g/files/ehbemt561/files/inline-documents/sonh/"
)
ARCHIVED = "https://web.archive.org/web/2018if_/http://sos.nh.gov/WorkArea/DownloadAsset.aspx?id="
PINNED = {
    "2022-ge-merrimack-county.xls": "20240114224201",
    "2018-sp-county-offices-sullivan-libertarian.pdf": "20240821033614",
}
PUBLISHER = "New Hampshire Secretary of State"
ALIASES = {
    "atkandgilmacgrant": "atkinsongilmantonacademygrant",
    "atkinsonandgilmantonacademygrant": "atkinsongilmantonacademygrant",
    "wentworthslocation": "wentworthlocation",
    "thompsonandmesspurchase": "thompsonandmeservespurchase",
}
OFFICES = (
    ("sheriff", "sheriff"),
    ("attorney", "district attorney"),
    ("countyattorney", "district attorney"),
    ("treasurer", "treasurer"),
    ("regofdeeds", "register of deeds"),
    ("registerofdeeds", "register of deeds"),
    ("regofprobate", "register of probate"),
    ("registerofprobate", "register of probate"),
)


def link(name: str) -> str:
    if name.startswith("asset-"):
        return ARCHIVED + name[6:].split(".")[0]
    return WAYBACK.replace("/web/2026id_/", f"/web/{PINNED[name]}id_/") + name if name in PINNED else WAYBACK + name


def years() -> set[int]:
    return {int(year) for year in os.environ.get("NH_YEARS", "2016,2018,2020,2022,2024").split(",")}


def listing() -> list[dict[str, str]]:
    return [
        dict(zip(("day", "stage", "county", "file"), line.split("\t"), strict=True))
        for line in FILES.read_text().splitlines()[1:]
    ]


def town(text: str) -> str:
    text = re.sub(r",\s*NH$|\s+ward\s*\d+$|\s+\(.*\)$", "", text.strip().rstrip("*").strip(), flags=re.IGNORECASE)
    text = text.lower().replace("&", "and").replace("'", "").replace("’", "")
    text = re.sub(r"\bpur\.?(?=\s|$)", "purchase", re.sub(r"\bgt\.?(?=\s|$)", "grant", text))
    text = re.sub(r"\bloc\.?(?=\s|$)", "location", text)
    text = re.sub(r"[^a-z]", "", re.sub(r"\s+(township|town|city)$", "", text))
    return ALIASES.get(text, text)


def office(label: str, title: str) -> str | None:
    letters = re.sub(r"[^a-z0-9]", "", label.lower())
    district = re.fullmatch(r"dist(?:rict)?(?:no)?(\d)", letters)
    if district:
        return f"board of commissioners:{district[1]}"
    if letters.startswith("countycommissioner") or letters.endswith("countycommissioners"):
        return "board of commissioners"
    for prefix, name in OFFICES:
        if letters.startswith(prefix):
            return name
    return None


def fetch() -> None:
    for entry in listing():
        if int(entry["day"][:4]) not in years():
            continue
        url = link(entry["file"])
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, url, suffix=Path(entry["file"]).suffix)
                break
            except httpx.HTTPError, OSError:
                time.sleep(5 * (attempt + 1))
        else:
            print(f"not fetched: {entry['file']}")
        time.sleep(1.2)


NUMBER = re.compile(r"\d[\d,]*(?:\.0+)?")
CANDIDATE = re.compile(
    r"scatter|no election|write-?ins?|w-in|(?:over|under)votes|(?:,\s*[a-z/&]{1,6}|\s+[rdli])\.?\*?$", re.IGNORECASE
)
DATED = re.compile(r"\d{4}-\d\d-\d\d")
WRITE_IN = re.compile(r"\bw-?in\b|write-?ins?", re.IGNORECASE)
OTHER = r"(?i)(?:scatter\w*|write-?ins?|others?)"


def pdf_grids(path: Path) -> list[tuple[str, list[list[str]]]]:
    text = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    words = sorted(
        (float(y0), float(x0), float(x1), html.unescape(word))
        for x0, y0, x1, word in re.findall(
            r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)<', text
        )
    )
    lines: list[list[tuple[float, float, str]]] = []
    top = -99.0
    for y0, x0, x1, word in words:
        if y0 - top > 2.5:
            lines.append([])
            top = y0
        lines[-1].append((x0, x1, word))
    cells: list[list[tuple[float, float, str]]] = []
    for line in lines:
        merged: list[tuple[float, float, str]] = []
        for x0, x1, word in sorted(set(line)):
            if merged and x0 - merged[-1][1] < 8:
                merged[-1] = (merged[-1][0], x1, f"{merged[-1][2]} {word}")
            else:
                merged.append((x0, x1, word))
        if merged[0][0] < 30 and sum(1 for c in merged if NUMBER.fullmatch(c[2])) > 2:
            merged = [c for index, c in enumerate(merged) if index == 0 or NUMBER.fullmatch(c[2]) or c[2] == "-"]
        if len(merged) == 1 and merged[0][2] in ("No", "Election"):
            continue
        if merged not in cells:
            cells.append(merged)

    def is_body(line: list[tuple[float, float, str]]) -> bool:
        return line[0][0] < 30 and len(line) > 1 and all(NUMBER.fullmatch(c[2]) or c[2] == "-" for c in line[1:])

    grids: list[tuple[str, list[list[str]]]] = []
    header: list[list[tuple[float, float, str]]] = []
    body: list[list[tuple[float, float, str]]] = []
    for line in [*cells, [(0.0, 0.0, "")]]:
        if is_body(line) or (body and line[0][0] < 30 and line[0][2].upper().startswith("TOTAL")):
            body.append(line)
            continue
        if body:
            names = [c for c in header[-1] if c[0] >= 30]
            columns: list[float] = []
            for x0 in sorted(c[0] for row in body for c in row[1:] if NUMBER.fullmatch(c[2])):
                if not columns or x0 - columns[-1] > 6:
                    columns.append(x0)
            if len(names) != len(columns):
                raise ValueError(f"{len(names)} names {[c[2] for c in names]} against {len(columns)} number columns")
            width = len(names) + 1
            rows = []
            for row in header:
                out = [""] * width
                if row is header[-1]:
                    for index, c in enumerate(names):
                        out[index + 1] = c[2]
                else:
                    centers = [(c[0] + c[1]) / 2 for c in names]
                    for c in row:
                        if c[0] < 30:
                            out[0] = c[2]
                            continue
                        near = min(range(len(names)), key=lambda i: abs(centers[i] - (c[0] + c[1]) / 2))
                        out[near + 1 if len(row) > 1 else 1] = c[2]
                rows.append(out)
            above = header[-2] if len(header) > 1 else []
            labels = [c for c in above if c[0] >= 30]
            if len(labels) > 1 and len(rows) > 1:
                out = [""] * width
                out[0] = " ".join(c[2] for c in above if c[0] < 30)
                centers = [(c[0] + c[1]) / 2 for c in names]
                owner = [
                    min(range(len(labels)), key=lambda k: abs((labels[k][0] + labels[k][1]) / 2 - m)) for m in centers
                ]
                for k, c in enumerate(labels):
                    first = next((i for i, o in enumerate(owner) if o == k), None)
                    if first is not None:
                        out[first + 1] = c[2]
                rows[-2] = out
            for row in body:
                out = [""] * width
                out[0] = row[0][2]
                for c in row[1:]:
                    index = min(range(len(columns)), key=lambda i: abs(columns[i] - c[0]))
                    if abs(columns[index] - c[0]) <= 6 and NUMBER.fullmatch(c[2]):
                        out[index + 1] = c[2]
                rows.append(out)
            grids.append((f"table{len(grids) + 1}", rows))
            header, body = [], []
        header.append(line)
    return grids


def blocks(path: Path, name: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    sheets = (
        pdf_grids(path)
        if path.suffix == ".pdf"
        else [
            (sheet_name, [[cell if cell else "" for cell in row] for row in sheet.rows()])
            for sheet_name, sheet in pl.read_excel(
                path, sheet_id=0, has_header=False, infer_schema_length=0, raise_if_empty=False
            ).items()
        ]
    )
    for sheet_name, raw_grid in sheets:
        grid = [[" ".join(cell.split()) if cell else "" for cell in row] for row in raw_grid]
        for row in grid:
            if DATED.match(row[0]):
                row[0] = ""

        def texts(row: list[str]) -> dict[int, str]:
            return {col: cell for col, cell in enumerate(row) if col and cell and not NUMBER.fullmatch(cell)}

        for index, row in enumerate(grid):
            names = texts(row)
            if index == 0 or not any(CANDIDATE.search(cell) for cell in names.values()) or row[0]:
                continue
            labels = texts(grid[index - 1])
            if not labels or any(CANDIDATE.search(cell) for cell in labels.values()):
                continue
            above = texts(grid[index - 2]) if index > 1 and not grid[index - 2][0] else {}
            title = next(iter(above.values())) if len(above) == 1 else ""
            body: list[list[str]] = []
            for line in grid[index + 1 :]:
                if not line[0]:
                    break
                body.append(line)
                if line[0].upper().startswith("TOTAL"):
                    break
            total = body.pop() if body and body[-1][0].upper().startswith("TOTAL") else None
            last: dict[str, object] | None = None
            for col, candidate in names.items():
                group = max((label for label in labels if label <= col), default=min(labels))
                if not any(col < len(line) and NUMBER.fullmatch(line[col]) for line in body):
                    continue
                if last is not None and candidate.lower().startswith("recount"):
                    found.remove(last)
                    candidate = str(last["candidate"])
                last = {
                    "file": name,
                    "sheet": sheet_name,
                    "title": title,
                    "heading": " ".join(" ".join(row) for row in grid[:3]).lower(),
                    "office": labels[group],
                    "candidate": candidate,
                    "column": col,
                    "rows": [
                        (line[0], int(float(line[col].replace(",", ""))))
                        for line in body
                        if col < len(line) and NUMBER.fullmatch(line[col])
                    ],
                    "total": int(float(total[col].replace(",", "")))
                    if total and col < len(total) and NUMBER.fullmatch(total[col])
                    else None,
                }
                found.append(last)
    return found


def parse() -> None:
    rows = []
    for entry in listing():
        if int(entry["day"][:4]) not in years():
            continue
        try:
            path = raw.cached(SOURCE, link(entry["file"])).path
            found = blocks(path, entry["file"])
        except Exception as error:
            print(f"{entry['file']}: {error!r}")
            continue
        stage = entry["stage"]
        bad = [
            b
            for b in found
            if stage != "general"
            and stage not in str(b["heading"])
            and any(p in str(b["heading"]) for p in ("democratic", "republican", "libertarian"))
        ]
        if bad:
            print(f"{entry['file']}: listed as {stage}, headed {bad[0]['heading'][:90]!r}; skipped")
            continue
        for block in found:
            mapped = office(str(block["office"]), str(block["title"]))
            if str(block["title"]).lower().startswith("county commissioner") and mapped is None:
                mapped = None
            for place, votes in block["rows"]:
                rows.append(
                    (
                        entry["day"],
                        entry["stage"],
                        str(block["sheet"]) if entry["county"] == "statewide" else entry["county"],
                        entry["file"],
                        str(block["title"]),
                        str(block["office"]),
                        mapped,
                        str(block["candidate"]),
                        int(block["column"]),
                        place,
                        votes,
                        block["total"],
                    )
                )
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.String,
            "stage": pl.String,
            "county": pl.String,
            "file": pl.String,
            "title": pl.String,
            "label": pl.String,
            "office": pl.String,
            "candidate": pl.String,
            "column": pl.Int32,
            "town": pl.String,
            "votes": pl.Int64,
            "printed": pl.Int64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "stage").agg(pl.col("file").n_unique(), pl.len()).sort("day", "stage"))


COLUMNS = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "supersedes_id", "completeness")
SUFFIX = re.compile(r",?\s*\b(?:jr|sr|ii|iii|iv)\b\.?$", re.IGNORECASE)
CONTEST = re.compile(r"^(\d{4}) (\w+) County (.+?) election(?:, District (\d))?(?:, (\w+) primary)?$")


def unbalanced(rows: pl.DataFrame) -> bool:
    sums = rows.group_by("candidate").agg(
        pl.col("votes").sum(), pl.col("printed").first(), pl.col("votes").first().alias("head")
    )
    return bool(
        sums.filter(
            pl.col("printed").is_not_null()
            & (pl.col("votes") != pl.col("printed"))
            & (pl.col("votes") - pl.col("head") != pl.col("printed"))
        ).height
    )


def near(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) <= 1
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def alike(letters: str, label: str) -> bool:
    other = surname(label, False).replace("rn", "m")
    letters = letters.replace("rn", "m")
    if other.endswith(letters) or letters.endswith(other):
        return True
    if len(letters) < 6:
        return False
    return letters in other or any(
        near(letters, other[len(other) - size :]) for size in range(len(letters) - 1, len(letters) + 2)
    )


def surname(text: str, party: bool = True) -> str:
    if party:
        text = re.sub(r"(?:,\s*[a-z/&]{1,6}|\s+[rdli])\.?$", "", text.strip(), flags=re.IGNORECASE)
    for _ in range(3):
        text = SUFFIX.sub("", text)
    return re.sub(r"[^a-z]", "", text.lower())


TITLES = {
    "sheriff": "Sheriff",
    "district attorney": "District Attorney",
    "board of commissioners": "Board of Commissioners",
}
TEMPLATE = (
    "kind",
    "office_id",
    "body_id",
    "district_area_id",
    "seat_id",
    "boundary_set_id",
    "magnitude",
    "party_scope_id",
    "round_number",
    "electoral_system_id",
    "default_result_kind",
    "term",
)


def create(
    conn: Connection,
    cs: Changeset,
    source: UUID,
    missing: list[tuple[tuple[int, str, str, str | None, str | None], pl.DataFrame]],
    report: dict[str, list[str]],
) -> list[tuple[UUID, pl.DataFrame, dict[str, UUID]]]:
    columns = ", ".join(f"c.{column}" for column in TEMPLATE)
    templates: dict[tuple[str, str, str | None], dict[str, object]] = {}
    scopes: dict[str, object] = {}
    elections: dict[tuple[int, bool], object] = {}
    people: dict[tuple[str, str, str], set[object]] = {}
    labels: dict[object, str] = {}
    for text, election, *rest in conn.execute(
        f"""select n.text, c.election_id, {columns} from external_id x join contest c on c.id = x.entity_id
            join name n on n.entity_id = c.id and n.is_preferred
            where x.scheme = 'us.contest' and x.value like 'NH:local:%%' and n.text like '%% County %% election%%'"""
    ).fetchall():
        found = CONTEST.match(text)
        if found is None:
            continue
        year, county, name, district, party = found.groups()
        row = dict(zip(TEMPLATE, rest, strict=True))
        templates.setdefault((county.lower(), name.lower(), district), row)
        elections.setdefault((int(year), party is not None), election)
        if party and row["party_scope_id"] is not None:
            scopes.setdefault(party.lower(), row["party_scope_id"])
    for county, name, person, label in conn.execute(
        """select lower(split_part(split_part(n.text, ' County ', 1), ' ', 2)), lower(regexp_replace(n.text, '^\\d{4} .+? County (.+?) election.*$', '\\1')),
                   ca.person_id, o.ballot_label
            from external_id x join name n on n.entity_id = x.entity_id and n.is_preferred
            join contest_option o on o.contest_id = x.entity_id and o.kind = 'candidate'
            join candidacy ca on ca.option_id = o.id
            where x.scheme = 'us.contest' and x.value like 'NH:local:%%' and n.text like '%% County %% election%%'"""
    ).fetchall():
        people.setdefault((county, name, surname(label, False)), set()).add(person)
        labels[person] = label
    taken = {row[0] for row in conn.execute("select path from slug where path like 'us/nh/%%-county/%%'").fetchall()}
    frames = []
    for key, rows in sorted(missing, key=lambda item: str(item[0])):
        year, county, name, district, party = key
        day = rows["day"][0]
        tag = f"{year} {county} {name} {district or ''} {party or 'general'}".replace("  ", " ")
        if unbalanced(rows):
            report.setdefault("towns do not add to the printed total", []).append(f"{tag} (new contest)")
            continue
        if not rows["votes"].sum():
            report.setdefault("no votes in the workbook", []).append(f"{tag} (new contest)")
            continue
        template = templates.get((county, name, district)) or templates.get((county, name, None))
        election = elections.get((year, party is not None))
        if template is None or election is None:
            report.setdefault("no contest and no sibling to copy from", []).append(tag)
            continue
        template = dict(template)
        if party is not None:
            template["party_scope_id"] = scopes.get(party)
            if template["party_scope_id"] is None:
                report.setdefault("no contest and no party scope", []).append(tag)
                continue
        else:
            template["party_scope_id"] = None
        title = f"{county.title()} County {TITLES[name]}"
        text = f"{year} {title} election" + (f", District {district}" if district else "")
        text += f", {party.title()} primary" if party else ""
        contest_key = (
            f"{day}:NH:{county}:{slug(TITLES[name])}"
            + (f"-{district}" if district else "")
            + (f"-{party}" if party else "")
        )
        contest = cs.key("us.contest", contest_key)
        cs.add("contest", id=contest, election_id=election, **template)
        cs.add("name", entity_id=contest, text=text, lang="en", kind="official", is_preferred=True)
        base = (
            f"us/nh/{county}-county/{day}-{'primary' if party else 'general'}/{slug(TITLES[name])}"
            + (f"-district-{district}" if district else "")
            + (f"-{party}" if party else "")
        )
        path = base
        if path in taken:
            path = f"{base}-2"
        taken.add(path)
        cs.add("slug", path=path, entity_id=contest)
        cs.add("citation", entity_id=contest, source_id=source)
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
        cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
        options: dict[str, UUID] = {}
        for position, candidate in enumerate(
            sorted(
                rows["candidate"].unique(maintain_order=True),
                key=lambda c: bool(WRITE_IN.search(c) or re.fullmatch(OTHER, c)),
            )
        ):
            option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
            options[candidate] = option
            if re.fullmatch(OTHER, candidate) or WRITE_IN.search(candidate):
                cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Others")
                continue
            letters = surname(candidate)
            known = people.get((county, name, letters), set())
            if len(known) == 1:
                (person,) = known
                shown = labels[person]
            else:
                person = cs.key("us.nh_sos_county.person", f"{county}:{name}:{letters}")
                shown = SUFFIX.sub(
                    "", re.sub(r",\s*[a-z/&]{1,6}\*?$", "", candidate.strip(), flags=re.IGNORECASE)
                ).strip(" *")
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=shown, lang="en", kind="official", is_preferred=True)
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position + 1,
                ballot_label=shown,
            )
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=shown,
                is_independent=False,
            )
        frames.append((result_set, rows, options))
    return frames


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet").filter(
        pl.col("office").is_not_null(), ~pl.col("candidate").str.contains(r"(?i)^(?:over|under)votes$")
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's county offices in the general and primary elections of "
            f"{', '.join(str(year) for year in sorted(years()))}, by town, from the Secretary of State's workbooks",
            polity="US-NH",
        )
        title = f"County office results by town, {min(years())}-{max(years())} general and state primary elections"
        source = cs.source(PUBLISHER, title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=title,
            url="https://www.sos.nh.gov/elections",
            license="Public record",
        )
        cell = ["day", "stage", "county", "office", "candidate", "town"]
        lines = lines.unique([*cell, "votes"], maintain_order=True)
        conflicts = lines.filter(pl.struct(cell).is_duplicated())
        if conflicts.height:
            print(f"{conflicts.height} cells differ between duplicate workbooks, dropped with their races")
            lines = lines.join(
                conflicts.select("day", "stage", "county", "office").unique(),
                on=["day", "stage", "county", "office"],
                how="anti",
            )
        for name in sorted(lines["file"].unique()):
            cs.artifact(raw.cached(SOURCE, link(name)), source)
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '33%%'"""
            ).fetchall()
        }
        places = {
            (code[:5] if scheme == "us.town" else None, town(text)): (scheme, code)
            for scheme, code, text in conn.execute(
                """select u.scheme_code, u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official' and n.is_preferred
                   where (u.scheme_code = 'us.town' or u.scheme_code = 'us.place') and u.code like '33%%'"""
            ).fetchall()
        }
        contests: dict[tuple[int, str, str, str | None, str | None], list[tuple[str, str, str]]] = {}
        for value, contest, text in conn.execute(
            """select x.value, x.entity_id, n.text from external_id x join name n on n.entity_id = x.entity_id
               where x.scheme = 'us.contest' and (x.value like 'NH:local:%%' or x.value ~ '^\\d{4}-\\d\\d-\\d\\d:NH:') and n.text like '%% County %% election%%'"""
        ).fetchall():
            found = CONTEST.match(text)
            if found is None:
                continue
            year, county, name, district, party = found.groups()
            contests.setdefault(
                (int(year), county.lower(), name.lower(), district, party.lower() if party else None), []
            ).append((value, contest, text))
        keyed: dict[tuple[int, str, str, str | None, str | None], pl.DataFrame] = {}
        for (day, stage, county, mapped), rows in lines.group_by("day", "stage", "county", "office"):
            name, _, district = mapped.partition(":")
            keyed[(int(day[:4]), county, name, district or None, None if stage == "general" else stage)] = rows
        report: dict[str, list[str]] = {}
        values = []
        used: set[tuple[int, str, str, str | None, str | None]] = set()
        missing: list[tuple[tuple[int, str, str, str | None, str | None], pl.DataFrame]] = []
        for key, rows in sorted(keyed.items(), key=lambda item: str(item[0])):
            year, county, name, district, party = key
            candidates = contests.get(key) or (
                contests.get((year, county, name, None, party)) if district is not None else None
            )
            tag = f"{year} {county} {name} {district or ''} {party or 'general'}".replace("  ", " ")
            if not candidates and district is None and name == "board of commissioners":
                spread = [
                    entry
                    for other, entries in contests.items()
                    if other[:3] == key[:3] and other[4] == party
                    for entry in entries
                ]
                candidates = spread if len(spread) == 1 else None
            if not candidates:
                if name in TITLES and key[4] in (None, "democratic", "republican", "libertarian"):
                    missing.append((key, rows))
                else:
                    report.setdefault("no contest", []).append(tag)
                continue
            if len(candidates) != 1:
                report.setdefault("two contests", []).append(f"{tag} {[c[0] for c in candidates]}")
                continue
            external, contest, _ = candidates[0]
            used.add(key)
            if conn.execute(
                """select 1 from result_set rs join changeset c on c.id = rs.changeset_id
                   where rs.contest_id = %s and rs.is_default and rs.status = 'active' and c.pipeline = %s""",
                [contest, SOURCE],
            ).fetchone():
                continue
            election, default, first = conn.execute(
                """select rs.election_id, rs.id, min(v.count_round)
                   from result_set rs join result_value v on v.result_set_k = rs.k
                   where rs.contest_id = %s and rs.is_default and rs.status = 'active' group by 1, 2""",
                [contest],
            ).fetchone() or (None, None, None)
            first = first or 1
            election = (
                election or conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
            )
            options = conn.execute(
                """select o.id::text, o.ballot_label, o.kind,
                          coalesce((select sum(v.value) from result_value v join result_set rs on rs.k = v.result_set_k
                                    where rs.id = %s and v.option_k = o.k and v.measure = 1), 0)::bigint
                   from contest_option o where o.contest_id = %s and o.parent_option_id is null""",
                [default, contest],
            ).fetchall()
            if unbalanced(rows):
                report.setdefault("towns do not add to the printed total", []).append(f"{tag} {external}")
                continue
            matched: dict[str, str] = {}
            folded: dict[str, str] = {}
            failed = None
            fresh: list[str] = []
            others = [option[0] for option in options if option[2] == "others"]
            order = sorted(
                rows["candidate"].unique(maintain_order=True),
                key=lambda name: bool(WRITE_IN.search(name) or re.fullmatch(OTHER, name)),
            )
            for candidate in order:
                if re.fullmatch(r"(?i)no election", candidate):
                    failed = "no election"
                    break
                if re.fullmatch(OTHER, candidate):
                    hits = [option for option in options if option[2] == "others"]
                else:
                    letters = surname(WRITE_IN.sub("", candidate).strip(" ()*"))
                    hits = [option for option in options if option[2] == "candidate" and alike(letters, option[1])]
                    if len(hits) > 1:
                        hits = [hit for hit in hits if hit[3]]
                        if len(hits) == 1:
                            report.setdefault("told apart by the compilation's votes", []).append(f"{tag} {candidate}")
                    if (
                        WRITE_IN.search(candidate)
                        and len(others) == 1
                        and (len(hits) != 1 or hits[0][0] in matched.values())
                    ):
                        folded[candidate] = others[0]
                        continue
                if not hits and not candidate.lower().startswith("recount"):
                    fresh.append(candidate)
                    continue
                if len(hits) != 1:
                    failed = f"{candidate} matches {[hit[1] for hit in hits]}"
                    break
                matched[candidate] = hits[0][0]
            if failed is None:
                matched.update(folded)
            if failed is not None:
                report.setdefault("candidate mismatch", []).append(f"{tag} {external}: {failed}")
                continue
            if failed is None and fresh:
                taken_positions = len(options)
                for offset, candidate in enumerate(fresh):
                    letters = surname(WRITE_IN.sub("", candidate).strip(" ()*"))
                    if (WRITE_IN.search(candidate) or re.fullmatch(OTHER, candidate)) and others:
                        matched[candidate] = others[0]
                        continue
                    option = cs.key("us.option", f"{external}:{slug(candidate)}")
                    if WRITE_IN.search(candidate) or re.fullmatch(OTHER, candidate):
                        cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Others")
                        others.append(str(option))
                        matched[candidate] = str(option)
                        continue
                    person = cs.key("us.nh_sos_county.person", f"{county}:{name}:{letters}")
                    shown = SUFFIX.sub(
                        "", re.sub(r",\s*[a-z/&]{1,6}\*?$", "", candidate.strip(), flags=re.IGNORECASE)
                    ).strip(" *")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=shown, lang="en", kind="official", is_preferred=True)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=taken_positions + offset + 1,
                        ballot_label=shown,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{external}:{slug(candidate)}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=shown,
                        is_independent=False,
                    )
                    matched[candidate] = str(option)
                    report.setdefault("option added from the workbook", []).append(f"{tag} {candidate}")
            named = [option for option in matched.values() if option not in others]
            if len(set(named)) != len(named):
                report.setdefault("two columns match one option", []).append(f"{tag} {external}")
                continue
            ours = {matched[c]: v for c, v in rows.group_by("candidate").agg(pl.col("votes").sum()).iter_rows()}
            theirs = {option[0]: option[3] for option in options if option[3]}
            if any(ours.get(option, 0) != theirs.get(option, 0) for option in {*ours, *theirs}):
                warded: dict[str, int] = {}
                for candidate, votes in (
                    rows.filter(pl.col("town").str.contains(r"(?i)\bward\s*\d"))
                    .group_by("candidate")
                    .agg(pl.col("votes").sum())
                    .iter_rows()
                ):
                    warded[matched[candidate]] = warded.get(matched[candidate], 0) + votes
                twice = all(
                    theirs.get(option, 0) == ours.get(option, 0) + warded.get(option, 0) for option in {*ours, *theirs}
                )
                gap = {
                    label: (theirs.get(oid, 0), ours.get(oid, 0))
                    for oid, label, _, _ in options
                    if theirs.get(oid, 0) != ours.get(oid, 0)
                }
                report.setdefault(
                    "differs: the compilation counts each city's wards twice" if twice else "differs: unexplained", []
                ).append(f"{tag} {external} {gap}")
            if not rows["votes"].sum() and rows["printed"].is_null().any():
                report.setdefault("no votes in the workbook", []).append(f"{tag} {external}")
                continue
            unit_codes: dict[str, tuple[str, str]] = {}
            for place in rows["town"].unique():
                hit = places.get((counties[county], town(place))) or places.get((None, town(place)))
                if hit is None:
                    raise ValueError(f"{county}: no unit for {place!r}")
                unit_codes[place] = hit
            result_set = cs.key(f"{SOURCE}.result_set", external)
            if default is not None:
                old = dict(
                    zip(
                        COLUMNS,
                        conn.execute(
                            f"select {', '.join(COLUMNS)} from result_set where id = %s", [default]
                        ).fetchone(),
                        strict=True,
                    )
                )
                cs.add("result_set", **old, status="superseded", is_default=False)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                supersedes_id=default,
                is_default=True,
                completeness=1,
            )
            cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
            declared = (
                conn.execute(
                    "select option_id::text, kind, seats from outcome where result_set_id = %s", [default]
                ).fetchall()
                if default is not None
                else []
            )
            winners = {option for option, kind, _ in declared if kind in ("elected", "won")}
            ranked = sorted((votes, option) for option, votes in ours.items() if option not in others)[::-1]
            if (
                winners
                and len(winners) > len(ranked)
                or winners
                and {option for votes, option in ranked if votes >= ranked[len(winners) - 1][0]} != winners
            ):
                report.setdefault("declared winner is not the top vote-getter, outcomes left off", []).append(
                    f"{tag} {external}"
                )
                declared = []
            for option, kind, seats in declared:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            values.append(
                rows.filter(pl.col("votes") > 0)
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("town").map_elements(
                        lambda place, codes=unit_codes: str(cs.unit(*codes[place])), return_dtype=pl.String
                    ),
                    option_id=pl.col("candidate").replace_strict(matched),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(first, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    votes=pl.col("votes"),
                )
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(value=pl.col("votes").sum())
            )
        for result_set, rows, options in create(conn, cs, source, missing, report) if missing else ():
            county = rows["county"][0]
            unit_codes = {}
            for place in rows["town"].unique():
                hit = places.get((counties[county], town(place))) or places.get((None, town(place)))
                if hit is None:
                    raise ValueError(f"{county}: no unit for {place!r}")
                unit_codes[place] = hit
            values.append(
                rows.filter(pl.col("votes") > 0)
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("town").map_elements(
                        lambda place, codes=unit_codes: str(cs.unit(*codes[place])), return_dtype=pl.String
                    ),
                    option_id=pl.col("candidate").replace_strict({c: str(o) for c, o in options.items()}),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(1, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    votes=pl.col("votes"),
                )
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(value=pl.col("votes").sum())
            )
        for reason, items in report.items():
            print(f"{len(items)}: {reason}")
            for item in items[:40]:
                print("   ", item)
        unmatched = [
            key
            for key in contests
            if key[0] in years() and key not in keyed and key[3] is not None or key[0] in years() and key not in keyed
        ]
        print(f"{len(values)} sets; {len(unmatched)} contests on file with no workbook block")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
