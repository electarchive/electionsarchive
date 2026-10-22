import json
import re
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
import polars as pl

from ea import raw
from ea.sources.us import statewide_totals
from ea.sources.us.wikipedia_primaries import STATES as PRIMARY_STATES
from ea.sources.us.wikipedia_primaries import clean, templates
from ea.store import DATA, connect

STATES = {**PRIMARY_STATES, "LA": "Louisiana"}
SOURCE = "us.wikipedia_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
NOTE = (
    "Interim totals from the election box in the English Wikipedia's article on this race, pinned to the revision "
    "current when it was fetched; the article cites the state's official register or canvass. Candidate rows sum to "
    "the box's total line, or to a total checked against the box's percentages, and the winner agrees with the "
    "office's holders where the archive records them. Write-in and scattering rows are left out. An official "
    "canvass or manual will supersede this set."
)
SKIP = re.compile(r"primar|run-?off|convention|caucus|nominat|special|recount|vacanc", re.IGNORECASE)
GENERIC = re.compile(
    r"^(write.?ins?|scattering|scattered|others?|all others|blanks?|void|over ?votes?|under ?votes?|none)\b",
    re.IGNORECASE,
)
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
MAJORITY_RUNOFF = {"GA": 1964, "LA": 1975}


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "formatversion": 2, "redirects": 1}
    )


def offices() -> list[tuple[str, int, str, str]]:
    with connect() as conn:
        return conn.execute(
            """select y.state, y.year, o.value,
                      (select n.text from name n where n.entity_id = y.office_id and n.is_preferred limit 1)
               from statewide_office_year y
               join external_id o on o.entity_id = y.office_id and o.scheme = 'us.office'
               where y.year < 2000 and not y.joint_ticket
               order by 1, 2, 3"""
        ).fetchall()


def variants(state: str, year: int, label: str) -> list[str]:
    name = STATES[state]
    label = label.removesuffix(f" of {name}")
    if label == "Lieutenant Governor":
        return [f"{year} {name} lieutenant gubernatorial election", f"{year} {name} Lieutenant Governor election"]
    found = [f"{year} {name} {label} election", f"{year} {name} {label[0].lower()}{label[1:]} election"]
    if not label.startswith("State "):
        found.append(f"{year} {name} State {label} election")
    return list(dict.fromkeys(found))


def read(url: str, download: bool) -> dict:
    try:
        artifact = raw.cached(SOURCE, url)
    except LookupError:
        if not download:
            raise
        for attempt in range(6):
            try:
                artifact = raw.fetch(SOURCE, url, suffix=".json")
                break
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in (429, 503) or attempt == 5:
                    raise
                time.sleep(20 * 2**attempt)
        time.sleep(0.4)
    return json.loads(artifact.path.read_text())


def resolve(download: bool) -> dict[tuple[str, int, str], str]:
    rows = offices()
    asked = {(state, year, value): variants(state, year, label) for state, year, value, label in rows}
    titles = sorted({title for options in asked.values() for title in options})
    found: dict[str, str] = {}
    for start in range(0, len(titles), 50):
        chunk = titles[start : start + 50]
        url = API + urlencode(
            {"action": "query", "titles": "|".join(chunk), "redirects": 1, "format": "json", "formatversion": 2}
        )
        body = read(url, download)["query"]
        missing = {entry["title"] for entry in body["pages"] if entry.get("missing") or entry.get("invalid")}
        normalized = {entry["from"]: entry["to"] for entry in body.get("normalized", [])}
        redirected = {entry["from"]: entry["to"] for entry in body.get("redirects", [])}
        for title in chunk:
            target = normalized.get(title, title)
            target = redirected.get(target, target)
            if target not in missing:
                found[title] = target
    resolved: dict[tuple[str, int, str], str] = {}
    for (state, year, value), options in asked.items():
        for title in options:
            target = found.get(title)
            if target is not None and re.match(rf"{year} {STATES[state]} .*\belection$", target):
                resolved[(state, year, value)] = target
                break
    return resolved


def fetch() -> None:
    for title in sorted(set(resolve(True).values())):
        read(page(title), True)


def box_label(text: str, start: int, params: dict[str, str]) -> str:
    headings = re.findall(r"^=+\s*(.*?)\s*=+\s*$", text[:start], re.MULTILINE)
    return f"{headings[-1] if headings else ''} {clean(params.get('title', ''))}"


def surname(text: str) -> str:
    folded = (
        unicodedata.normalize("NFKD", re.sub(r"\(.*?\)|['’`]", "", text)).encode("ascii", "ignore").decode().lower()
    )
    words = [word for word in re.findall(r"[a-z]+", folded) if word not in SUFFIXES]
    return words[-1] if words else ""


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    found = resolve(False)
    labels = {value: label for _, _, value, label in offices()}
    lines: list[dict[str, object]] = []
    held: list[tuple[str, str]] = []
    for (state, year, value), title in sorted(found.items()):
        office = value.split(":")[0]
        if year >= MAJORITY_RUNOFF.get(state, 9999):
            held.append((f"{year} {state} {office}", "majority-vote runoff state"))
            continue
        content = read(page(title), False)
        text = content["parse"]["wikitext"]
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        text = re.sub(r"<ref[^>]*/\s*>|<ref(?:\s[^>]*[^/])?>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
        infobox = next((params for name, params in templates(text) if name.startswith("infobox election")), {})
        stated = infobox.get("election_date", "")
        if parts := re.search(r"([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})", stated):
            day = datetime.strptime(f"{parts[1]} {parts[2]} {parts[3]}", "%B %d %Y").date()
        elif parts := re.search(r"(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})", stated):
            day = datetime.strptime(f"{parts[1]} {parts[2]} {parts[3]}", "%d %B %Y").date()
        elif parts := re.search(r"start date\|(\d{4})\|(\d{1,2})\|(\d{1,2})", stated, re.IGNORECASE):
            day = date(int(parts[1]), int(parts[2]), int(parts[3]))
        else:
            held.append((title, "no election date"))
            continue
        if day.year != year:
            held.append((title, f"election date {day} is not in {year}"))
            continue
        boxes = []
        for begin in re.finditer(r"\{\{\s*election box begin", text, re.IGNORECASE):
            end = re.search(r"\{\{\s*election box end\s*\}\}", text[begin.start() :], re.IGNORECASE)
            body = text[begin.start() : begin.start() + end.end()] if end else text[begin.start() :]
            members = templates(body)
            params = members[0][1] if members else {}
            label = box_label(text, begin.start(), params)
            if not SKIP.search(label):
                boxes.append((label, members[1:]))
        if len(boxes) > 1:
            boxes = [box for box in boxes if re.search(r"general", box[0], re.IGNORECASE)] or boxes
        if len(boxes) != 1:
            held.append((title, f"{len(boxes)} general-election boxes"))
            continue
        rows, listed, total = [], 0, None
        for name, params in boxes[0][1]:
            digits = re.sub(r"\D", "", clean(params.get("votes", "")))
            if name.startswith("election box total"):
                total = int(digits) if digits else None
            elif name.startswith("election box") and "candidate" in params and digits:
                listed += int(digits)
                candidate = clean(params["candidate"])
                if candidate and not GENERIC.match(candidate):
                    label = re.sub(r"\(.*?\)", "", clean(params.get("party", ""))).replace("–", "-")
                    label = re.sub(rf"\s*\b(?:of )?{STATES[state]}\b", "", label)
                    label = re.sub(r"\s+of(?: the)?(?: United States)?(?: of)?(?: America)?$", "", label).strip()
                    if re.match(
                        r"independent\b(?!.*(?:republican|democrat))|nonpartisan|write-?in", label, re.IGNORECASE
                    ):
                        label = "Independent"
                    elif state == "MN" and label.startswith("Democratic") and "Labor" in label:
                        label = "Minnesota Democratic-Farmer-Labor Party"
                    rows.append(
                        {
                            "name": candidate,
                            "party": label or None,
                            "votes": int(digits),
                            "winner": 1 if "winning" in name else None,
                            "percentage": params.get("percentage") or params.get("%"),
                        }
                    )
        if len(rows) < 1:
            held.append((title, "no candidate rows with votes"))
            continue
        if total is not None:
            if abs(listed - total) > max(5, total // 200):
                held.append((title, f"rows sum {listed}, total line {total}"))
                continue
        else:
            lead = max(rows, key=lambda row: row["votes"])
            percent = re.sub(r"[^\d.]", "", str(lead["percentage"] or ""))
            if not percent or abs(lead["votes"] * 100 / listed - float(percent)) > 1.5:
                held.append((title, "no total line and percentages do not confirm the rows"))
                continue
        if sum(row["winner"] == 1 for row in rows) > 1:
            held.append((title, "more than one winning row"))
            continue
        label = re.sub(rf" of {STATES[state]}$", "", labels[value])
        for row in rows:
            lines.append(
                {
                    "source": title,
                    "state": state,
                    "date": day,
                    "office": office,
                    "title": label,
                    "name": row["name"],
                    "party": row["party"],
                    "votes": row["votes"],
                    "winner": row["winner"],
                    "page": None,
                }
            )
    frame = pl.DataFrame(
        lines,
        schema={
            "source": pl.String,
            "state": pl.String,
            "date": pl.Date,
            "office": pl.String,
            "title": pl.String,
            "name": pl.String,
            "party": pl.String,
            "votes": pl.Int64,
            "winner": pl.Int64,
            "page": pl.Int64,
        },
    )
    contest = ["state", "date", "office"]
    doubled = frame.group_by(*contest, "name").len().filter(pl.col("len") > 1)
    for state, day, office, _, _ in doubled.iter_rows():
        held.append((f"{day} {state} {office}", "a name appears twice"))
    frame = frame.join(doubled.select(contest).unique(), on=contest, how="anti")
    leaders = (
        frame.sort("votes", descending=True)
        .group_by(*contest, maintain_order=True)
        .agg(
            pl.col("name").first().alias("leader"),
            pl.col("name").filter(pl.col("winner") == 1).first().alias("declared"),
            (pl.col("votes").first() == pl.col("votes").get(1, null_on_oob=True)).alias("tied"),
        )
    )
    with connect() as conn:
        tenures = conn.execute(
            """select o.value, t.from_date, n.text from tenure t
               join external_id o on o.entity_id = t.office_id and o.scheme = 'us.office'
               join name n on n.entity_id = t.person_id and n.is_preferred
               where t.from_date is not null"""
        ).fetchall()
    holders: dict[str, list[tuple[date, str]]] = {}
    for value, start, holder in tenures:
        holders.setdefault(value, []).append((start, surname(holder)))
    dropped = []
    for state, day, office, leader, declared, tied in leaders.iter_rows():
        winner = declared or leader
        if declared is None and tied:
            dropped.append((state, day, office))
            held.append((f"{day} {state} {office}", "a tie with no declared winner"))
            continue
        window = [name for start, name in holders.get(f"{office}:{state}", []) if 0 <= (start - day).days <= 600]
        if window and surname(winner) not in window:
            dropped.append((state, day, office))
            held.append((f"{day} {state} {office}", f"winner {winner} is not among the holders {sorted(set(window))}"))
    for state, day, office in dropped:
        frame = frame.filter(~((pl.col("state") == state) & (pl.col("date") == day) & (pl.col("office") == office)))
    frame.write_parquet(PARSED / "lines.parquet")
    pl.DataFrame(held, schema=["subject", "reason"], orient="row").write_csv(PARSED / "held.tsv", separator="\t")
    print(
        frame.group_by("state").agg(pl.struct("date", "office").n_unique().alias("contests")).sort("state"),
        f"{frame.select('state', 'date', 'office').unique().height} contests, {len(held)} held back",
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    book = {
        title: {
            "publisher": "Wikipedia",
            "title": title,
            "url": page(title),
            "landing": f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
            "license": "CC BY-SA 4.0",
            "page_offset": 0,
            "kind": "community",
        }
        for title in frame["source"].unique()
    }
    return statewide_totals.emit(
        SOURCE,
        VERSION,
        "Statewide executive offices by state total from the English Wikipedia's election articles",
        frame,
        book,
        NOTE,
    )
