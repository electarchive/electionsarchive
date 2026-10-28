import json
import re
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import fec
from ea.store import DATA, connect

SOURCE = "us.wikipedia_primaries"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
PRIMARY = {
    "AL": date(2024, 3, 5),
    "AK": date(2024, 8, 20),
    "AZ": date(2024, 7, 30),
    "AR": date(2024, 3, 5),
    "CA": date(2024, 3, 5),
    "CO": date(2024, 6, 25),
    "CT": date(2024, 8, 13),
    "DE": date(2024, 9, 10),
    "FL": date(2024, 8, 20),
    "GA": date(2024, 5, 21),
    "HI": date(2024, 8, 10),
    "ID": date(2024, 5, 21),
    "IL": date(2024, 3, 19),
    "IN": date(2024, 5, 7),
    "IA": date(2024, 6, 4),
    "KS": date(2024, 8, 6),
    "KY": date(2024, 5, 21),
    "ME": date(2024, 6, 11),
    "MD": date(2024, 5, 14),
    "MA": date(2024, 9, 3),
    "MI": date(2024, 8, 6),
    "MN": date(2024, 8, 13),
    "MS": date(2024, 3, 12),
    "MO": date(2024, 8, 6),
    "MT": date(2024, 6, 4),
    "NE": date(2024, 5, 14),
    "NV": date(2024, 6, 11),
    "NH": date(2024, 9, 10),
    "NJ": date(2024, 6, 4),
    "NM": date(2024, 6, 4),
    "NY": date(2024, 6, 25),
    "NC": date(2024, 3, 5),
    "ND": date(2024, 6, 11),
    "OH": date(2024, 3, 19),
    "OK": date(2024, 6, 18),
    "OR": date(2024, 5, 21),
    "PA": date(2024, 4, 23),
    "RI": date(2024, 9, 10),
    "SC": date(2024, 6, 11),
    "SD": date(2024, 6, 4),
    "TN": date(2024, 8, 1),
    "TX": date(2024, 3, 5),
    "UT": date(2024, 6, 25),
    "VT": date(2024, 8, 13),
    "VA": date(2024, 6, 18),
    "WA": date(2024, 8, 6),
    "WV": date(2024, 5, 14),
    "WI": date(2024, 8, 13),
    "WY": date(2024, 8, 20),
}
RUNOFF = {
    "AL": date(2024, 4, 16),
    "AR": date(2024, 4, 2),
    "GA": date(2024, 6, 18),
    "MS": date(2024, 4, 2),
    "NC": date(2024, 5, 14),
    "OK": date(2024, 8, 27),
    "SC": date(2024, 6, 25),
    "SD": date(2024, 7, 30),
    "TX": date(2024, 5, 28),
}
SYSTEMS = {"NC": "two_round_30", "SD": "two_round_35", "CA": "top_two", "WA": "top_two", "AK": "top_four"}
AT_LARGE = {"AK", "DE", "ND", "SD", "VT", "WY"}
SENATE = {
    "AZ", "CA", "CT", "DE", "FL", "HI", "IN", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NJ", "NM",
    "NY", "ND", "OH", "PA", "RI", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}  # fmt: skip
DATES = {
    "Template:2024IADem": date(2024, 3, 5),
    "Template:2024DCRep": date(2024, 3, 3),
    "Template:2024LARep": date(2024, 3, 23),
}
GOVERNOR = {"DE", "IN", "MO", "MT", "NH", "NC", "ND", "UT", "VT", "WA", "WV"}
PARTIES = [
    (r"democratic.farmer.labor|\bdfl\b", "minnesota-democratic-farmer-labor", "MN:minnesota-democratic-farmer-labor-party"),
    (r"democratic.nonpartisan league|democratic.npl", "north-dakota-democratic-nonpartisan-league", "ND:north-dakota-democratic-nonpartisan-league-party"),
    (r"democrat", "democratic", "democratic-party"),
    (r"republican", "republican", "republican-party"),
    (r"libertarian", "libertarian", "libertarian-party"),
    (r"green", "green", "green-party"),
    (r"constitution", "constitution", "constitution-party"),
    (r"conservative", "conservative", "NY:conservative-party"),
    (r"working families", "working-families", "NY:working-families-party"),
    (r"progressive", "progressive", "VT:progressive-party"),
    (r"legal marijuana now", "legal-marijuana-now", "legal-marijuana-now"),
]  # fmt: skip
WRITE_IN = re.compile(r"write.?ins?\b|^scatter|^other candidates$", re.IGNORECASE)
BLANK = re.compile(r"^(blanks?|over ?votes?|under ?votes?|spoiled|void)$", re.IGNORECASE)
NOTA = re.compile(r"^none of (these|the above|the names)", re.IGNORECASE)
TITLE = "2024 United States primary elections"
NOTE = (
    "Statewide and district primary totals as transcribed in the English Wikipedia's 2024 election articles, which "
    "cite the state canvasses."
)
PRESIDENTIAL = (
    "Statewide totals from the results table in the English Wikipedia's article on this primary or caucus, pinned "
    "to the revision current when it was fetched. Delegate counts are not recorded here."
)
METHOD = (
    "Totals from the election box in the English Wikipedia's article on this race, pinned to the revision current "
    "when it was fetched. The candidate rows sum to the box's total line."
)


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "formatversion": 2, "redirects": 1}
    )


def titles() -> list[tuple[str, str, str]]:
    found = []
    for state, name in STATES.items():
        found.append(
            (
                state,
                "house",
                f"2024 United States House of Representatives election{'' if state in AT_LARGE else 's'} in {name}",
            )
        )
        if state in SENATE:
            found.append((state, "senate", f"2024 United States Senate election in {name}"))
        if state in GOVERNOR:
            found.append((state, "governor", f"2024 {name} gubernatorial election"))
    found.append(("NE", "senate:special", "2024 United States Senate special election in Nebraska"))
    return found


def contests() -> list[tuple[str, str, str]]:
    return [
        (state, party, f"2024 {name} {party} presidential {event}")
        for state, name in [*STATES.items(), ("LA", "Louisiana"), ("DC", "District of Columbia")]
        for party in ("Democratic", "Republican")
        for event in ("primary", "caucuses", "caucus")
    ]


def tables(text: str) -> list[str]:
    return [
        f"Template:{name}" for name in dict.fromkeys(re.findall(r"\{\{\s*(2024 ?[A-Z]{2} ?[A-Za-z]*)\s*\}\}", text))
    ]


def fetch() -> None:
    for _, _, title in titles():
        raw.fetch(SOURCE, page(title), suffix=".json")
    for _, _, title in contests():
        content = json.loads(raw.fetch(SOURCE, page(title), suffix=".json").path.read_text())
        for template in tables(content.get("parse", {}).get("wikitext", "")):
            raw.fetch(SOURCE, page(template), suffix=".json")


def templates(text: str) -> list[tuple[str, dict[str, str]]]:
    found: list[tuple[str, dict[str, str]]] = []
    depth, start = 0, 0
    index = 0
    while index < len(text) - 1:
        pair = text[index : index + 2]
        if pair == "{{":
            if depth == 0:
                start = index
            depth += 1
            index += 2
        elif pair == "}}" and depth:
            depth -= 1
            index += 2
            if depth == 0:
                body = text[start + 2 : index - 2]
                parts, level, current = [], 0, ""
                for character in body:
                    level += character == "{"
                    level -= character == "}"
                    if character == "|" and level == 0:
                        parts.append(current)
                        current = ""
                    else:
                        current += character
                parts.append(current)
                params = {}
                for part in parts[1:]:
                    key, sep, value = part.partition("=")
                    if sep:
                        params[key.strip().lower()] = value.strip()
                found.append((re.sub(r"\s+", " ", parts[0]).strip().lower(), params))
        else:
            index += 1
    return found


def clean(text: str) -> str:
    text = re.sub(r"\{\{\s*(?:nowrap|nobr|small|nbsp|sort\|[^|{}]*)\s*\|([^{}]*)\}\}", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"'{2,}|<[^>]+>", "", text)
    text = re.sub(r"\((?:incumbent|withdr[ae]wn?|disqualified|deceased|write.in)[^)]*\)", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,*")


def party_of(text: str) -> tuple[str, str] | None:
    lowered = text.lower()
    for pattern, suffix, key in PARTIES:
        if re.search(pattern, lowered):
            return suffix, key
    return None


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for state, office, title in titles():
        content = json.loads(raw.cached(SOURCE, page(title)).path.read_text())
        if "parse" not in content:
            print(f"{title}: no such page")
            continue
        if (state, office) in seen:
            continue
        seen.add((state, office))
        text = content["parse"]["wikitext"]
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        text = re.sub(r"<ref[^>]*/\s*>|<ref(?:\s[^>]*[^/])?>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
        stated = re.search(
            r"primar(?:y|ies)[^.\n]{0,60}?\b(?:on|held)\s+((?:January|February|March|April|May|June|July|August|September) \d{1,2}, 2024)",
            text,
        )
        if stated and office == "house":
            day = date.strptime(stated[1], "%B %d, %Y")
            if day != PRIMARY[state]:
                print(f"{state}: the page states a primary on {day}, not {PRIMARY[state]}")
        headings = [
            (m.start(), len(m[1]), m[2].strip()) for m in re.finditer(r"^(=+)\s*(.*?)\s*=+\s*$", text, re.MULTILINE)
        ]
        for box in re.finditer(
            r"\{\{\s*Election box (?:open primary )?begin.*?\{\{\s*Election box end\s*\}\}",
            text,
            re.DOTALL | re.IGNORECASE,
        ):
            stack: dict[int, str] = {}
            for position, level, heading in headings:
                if position > box.start():
                    break
                stack = {key: value for key, value in stack.items() if key < level}
                stack[level] = heading
            context = " / ".join(stack[level] for level in sorted(stack))
            parsed = templates(box[0])
            begin = parsed[0]
            label = clean(begin[1].get("title", ""))
            blanket = "open primary" in begin[0] or state in SYSTEMS and SYSTEMS[state].startswith("top_")
            primary_text = f"{label} / {context}"
            if re.search(r"special|convention|caucus|general election|nominating", label, re.IGNORECASE) or re.search(
                r"special|convention", context, re.IGNORECASE
            ):
                continue
            if not re.search(r"primary|run.?off", primary_text, re.IGNORECASE) and not (
                blanket and "open primary" in begin[0]
            ):
                continue
            if re.search(r"\bpresidential\b|\bresults by county\b", label, re.IGNORECASE):
                continue
            runoff = state in RUNOFF and bool(
                re.search(
                    r"run.?off", " ".join([label, *[stack[level] for level in sorted(stack)][-2:]]), re.IGNORECASE
                )
            )
            found = party_of(label) or (None if blanket else party_of(context))
            if found is None and not blanket:
                print(f"{state} {office}: no party in {label!r} under {context!r}")
                continue
            district = None
            if office == "house":
                if state in AT_LARGE:
                    district = 0
                else:
                    numbered = re.search(r"District (\d+)", context) or re.search(
                        r"(\d+)(?:st|nd|rd|th) congressional district", label
                    )
                    if numbered is None:
                        print(f"{state} house: no district for {label!r} under {context!r}")
                        continue
                    district = int(numbered[1])
            total = None
            entries: list[tuple[str, str, int | None, bool, str]] = []
            for name, params in parsed[1:]:
                if "open primary general election" in name:
                    break
                if name.startswith("election box total"):
                    digits = re.sub(r"[^\d]", "", clean(params.get("votes", "")))
                    total = int(digits) if digits else None
                    continue
                if not re.match(r"election box (winning )?candidate|election box write", name):
                    continue
                candidate = clean(params.get("candidate", params.get("name", "")))
                digits = re.sub(r"[^\d]", "", clean(params.get("votes", "")))
                if not candidate or BLANK.match(candidate):
                    continue
                entries.append(
                    (
                        candidate,
                        clean(params.get("party", "")),
                        int(digits) if digits else None,
                        "winning" in name,
                        "write_in"
                        if "write" in name or WRITE_IN.search(candidate)
                        else "none_of_the_above"
                        if NOTA.match(candidate)
                        else "candidate",
                    )
                )
            if all(votes is None for _, _, votes, _, _ in entries):
                continue
            counted = sum(votes or 0 for _, _, votes, _, _ in entries)
            party, scope = (None, None) if blanket or found is None else found
            for candidate, label_of, votes, winner, kind in entries:
                rows.append(
                    {
                        "state": state,
                        "office": office,
                        "district": district,
                        "party": party,
                        "scope": scope,
                        "runoff": runoff,
                        "title": title,
                        "box": label,
                        "context": context,
                        "position": box.start(),
                        "total": total,
                        "counted": counted,
                        "candidate": candidate,
                        "label": label_of,
                        "votes": votes,
                        "winner": winner,
                        "kind": kind,
                    }
                )
    presidential: list[dict[str, object]] = []
    used: set[str] = set()
    for state, party, title in contests():
        content = json.loads(raw.cached(SOURCE, page(title)).path.read_text())
        resolved = content.get("parse", {}).get("title", "")
        if not re.search(r"presidential (primar|caucus|nominating)", resolved):
            continue
        article = content["parse"]["wikitext"]
        stated = re.search(r"election_date\s*=\s*([A-Z][a-z]+ \d{1,2}, 2024)", article)
        for template in tables(article):
            found = re.fullmatch(r"Template:2024 ?([A-Z]{2}) ?([A-Za-z]*)", template)
            if found is None or found[1] != state or template in used:
                continue
            suffix = found[2]
            side = "Republican" if "Rep" in suffix else "Democratic" if "Dem" in suffix else party
            if side != party and side.lower() not in resolved.lower():
                continue
            used.add(template)
            text = json.loads(raw.cached(SOURCE, page(template)).path.read_text())["parse"]["wikitext"]
            text = re.sub(r"<noinclude>.*?</noinclude>|<!--.*?-->", "", text, flags=re.DOTALL)
            text = re.sub(r"<ref[^>]*/\s*>|<ref(?:\s[^>]*[^/])?>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
            caption = re.search(r"^\|\+(.*)$", text, re.MULTILINE)
            label = clean(caption[1]) if caption else ""
            dated = re.search(r"([A-Z][a-z]+ \d{1,2}, 2024)", label) or stated
            day = DATES.get(template) or (date.strptime(dated[1], "%B %d, %Y") if dated else None)
            if day is None:
                print(f"{template}: no date")
                continue
            caucus = "caucus" in f"{suffix} {label}".lower()
            total = None
            spoiled = 0
            for chunk in re.split(r"^\|-.*$", text, flags=re.MULTILINE):
                cells: list[str] = []
                for line in chunk.split("\n"):
                    if not line.startswith(("|", "!")) or line.startswith(("|+", "|}", "{|")):
                        continue
                    for cell in re.split(r"\|\||!!", line[1:]):
                        cell = re.sub(
                            r"^(?:\s*(?:scope|style|colspan|rowspan|class|align|data-sort-value)=[^|]*)+\|", "", cell
                        )
                        cells.append(clean(cell))
                if len(cells) < 2:
                    continue
                digits = re.sub(r"[^\d]", "", cells[1]) if not re.search(r"[A-Za-z%.]", cells[1]) else ""
                if re.match(r"total", cells[0], re.IGNORECASE):
                    total = int(digits) if digits else None
                    continue
                if not digits or re.fullmatch(r"candidate", cells[0], re.IGNORECASE):
                    continue
                if re.match(
                    r'"?(?:blank\b|void\b|over ?votes?\b|under ?votes?\b|over and under)', cells[0], re.IGNORECASE
                ):
                    spoiled += int(digits)
                    continue
                candidate = re.sub(r"\s*\((?:Republican|Democrat|Democratic|Ind\.|Independent)\)$", "", cells[0])
                kind = (
                    "uncommitted"
                    if re.match(r"uncommitted|noncommitted|no preference|uninstructed", candidate, re.IGNORECASE)
                    else "write_in"
                    if re.search(r"write.?in", candidate, re.IGNORECASE) or re.fullmatch(r'".*"', candidate)
                    else "none_of_the_above"
                    if NOTA.match(candidate)
                    else "others"
                    if re.fullmatch(r"others?|other candidates", candidate, re.IGNORECASE)
                    else "candidate"
                )
                presidential.append(
                    {
                        "state": state,
                        "party": side,
                        "day": day,
                        "caucus": caucus,
                        "template": template,
                        "candidate": candidate.strip('"'),
                        "votes": int(digits),
                        "kind": "write_in" if candidate != cells[0] else kind,
                        "named": kind == "candidate",
                    }
                )
            if total is not None:
                total -= spoiled
            counted = 0
            for row in presidential:
                if row["template"] == template:
                    row["total"] = total
                    counted += int(str(row["votes"]))
            if total is not None and total != counted:
                print(f"{template}: rows add up to {counted:,}, the total line says {total:,}")
    sides: dict[str, dict[str, int]] = {}
    for row in presidential:
        if row["kind"] == "candidate":
            counts = sides.setdefault(str(row["candidate"]), {})
            counts[str(row["party"])] = counts.get(str(row["party"]), 0) + 1
    for row in presidential:
        counts = sides.get(str(row["candidate"]), {})
        if row["kind"] == "candidate" and len(counts) > 1 and counts[str(row["party"])] < max(counts.values()):
            print(f"{row['template']}: {row['candidate']} runs in the other party's primaries, a write-in")
            row["kind"] = "write_in"
    pl.DataFrame(presidential).write_parquet(PARSED / "presidential.parquet")
    frame = pl.DataFrame(rows, infer_schema_length=None)
    race = ["state", "office", "district", "party"]
    frame = frame.with_columns(
        boxes=pl.col("position").n_unique().over(race), rank=pl.col("position").rank("dense").over(race)
    ).with_columns(
        runoff=pl.when(pl.col("boxes") == 2, pl.col("party").is_not_null())
        .then(pl.col("rank") == 2)
        .otherwise("runoff")
    )
    frame = frame.filter(pl.col("party").is_not_null() | (pl.col("rank") == pl.col("boxes")))
    crowded = frame.filter(
        (pl.col("boxes") > 2)
        | (pl.col("boxes") == 2) & ~pl.col("state").is_in(list(RUNOFF)) & pl.col("party").is_not_null()
    )
    if crowded.height:
        print(crowded.select(*race, "boxes", "box", "context").unique().sort(race))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "boxes.parquet")
    races = frame.group_by("state", "office", "district", "party", "runoff", "position").agg(
        pl.len(), pl.col("total").first(), pl.col("counted").first(), pl.col("box").first()
    )
    print(races.group_by("office", "runoff").len().sort("office", "runoff"))
    unequal = races.filter(pl.col("total").is_not_null() & (pl.col("total") != pl.col("counted")))
    print(f"{unequal.height} boxes whose rows do not sum to the total")
    print(unequal.sort("state").head(60))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "boxes.parquet")
    with connect() as conn:
        generals: dict[str, dict[str, object]] = {}
        nominees: dict[str, list[tuple[str, UUID]]] = {}
        for row in conn.execute(
            """select x.value, c.kind, c.office_id, c.body_id, c.seat_id, c.district_area_id, c.boundary_set_id,
                      c.term, (select u.id from result_set rs join result_value v on v.result_set_k = rs.k
                               join reporting_unit u on u.k = v.unit_k and u.scheme_code in ('us.cd', 'us.state')
                               where rs.contest_id = c.id and rs.is_default limit 1),
                      (select array_agg(n.text) from contest_option o
                       join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                       join name n on n.entity_id = cy.person_id and n.is_preferred where o.contest_id = c.id),
                      (select array_agg(cy.person_id) from contest_option o
                       join candidacy cy on cy.option_id = o.id and cy.role = 'head' where o.contest_id = c.id)
               from external_id x join contest c on c.id = x.entity_id
               where x.scheme = 'us.contest' and x.value ~ '^2024-11-05:[A-Z]{2}:(house:[0-9]+|senate(:special)?|governor)$'"""
        ).fetchall():
            generals[row[0]] = dict(
                zip(
                    ("kind", "office_id", "body_id", "seat_id", "district_area_id", "boundary_set_id", "term", "unit"),
                    row[1:9],
                    strict=True,
                )
            )
            nominees[row[0]] = list(zip(row[9] or [], row[10] or [], strict=True))
        keys = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.contest'")}
        elections = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        fec_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'fec.candidate'")}
        slugs = dict(conn.execute("select path, entity_id from slug").fetchall())
        areas: dict[str, UUID] = {}
        states = {
            abbreviation: unit
            for abbreviation, unit, areas[abbreviation] in conn.execute(
                """select substr(p.iso_code, 4), u.id, u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        roster: dict[tuple[str, str, int], list[tuple[str, set[str]]]] = {}
        with zipfile.ZipFile(raw.cached(fec.SOURCE, fec.MASTERS[2024]).path) as archive:
            for record in archive.read("cn.txt").decode("latin-1").splitlines():
                fec_id, filed, _, _, place, office, numbered = record.split("|")[:7]
                if office == "P":
                    roster.setdefault(("US", "P", 0), []).append(
                        (fec_id, set(slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", filed)).split("-")))
                    )
                if office in ("H", "S"):
                    roster.setdefault((place, office, int(numbered or 0) if office == "H" else 0), []).append(
                        (fec_id, set(slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", filed)).split("-")))
                    )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2024 presidential, House, Senate, and governor primaries and runoffs from the English Wikipedia",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/2024_United_States_elections",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        for _, _, title in titles():
            cs.artifact(raw.cached(SOURCE, page(title)), source)
        systems: dict[str, UUID] = {}
        persons: set[str] = set()
        values: list[tuple[str, str, str, int]] = []
        contests: dict[tuple[str, str, int | None, str | None], UUID] = {}
        race = ["state", "office", "district", "party", "runoff"]
        skipped = 0
        for (state, office, district, party, runoff), lines in frame.sort(
            "state", "office", "district", "runoff", "party", nulls_last=True
        ).group_by(race, maintain_order=True):
            day = RUNOFF[state] if runoff else PRIMARY[state]
            general_key = f"2024-11-05:{state}:{office}" + (f":{district}" if office == "house" else "")
            general = generals.get(general_key)
            seat = f"{office}:{district}" if office == "house" else office
            contest_key = f"{day}:{state}:{seat}" + (f":{party}" if party else "")
            if general is None:
                print(f"{contest_key}: no 2024 general contest to model it on, skipped")
                skipped += 1
                continue
            if contest_key in keys:
                print(f"{contest_key}: already held, skipped")
                skipped += 1
                continue
            keys.add(contest_key)
            kind = "runoff" if runoff else "primary"
            election_key = f"{day}:{state}:{kind}"
            election = cs.key("us.election", election_key)
            if election_key not in elections:
                elections.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    election,
                    f"{day:%B} {day.day}, 2024 {STATES[state]} primary runoff election"
                    if runoff
                    else f"2024 {STATES[state]} primary election",
                )
                cs.add("slug", path=f"us/{state.lower()}/{day}-{kind}", entity_id=election)
            scope = str(lines["scope"][0]) if party else None
            shown = next(
                (
                    label
                    for pattern, suffix, label in (
                        ("minnesota-democratic-farmer-labor", "", "Democratic–Farmer–Labor"),
                        ("north-dakota-democratic-nonpartisan-league", "", "Democratic-NPL"),
                    )
                    if party == pattern
                ),
                (party or "").replace("-", " ").title(),
            )
            blanket = party is None
            system_key = SYSTEMS.get(state, "two_round" if state in RUNOFF else "fptp")
            if not blanket and system_key.startswith("top_"):
                system_key = "fptp"
            if system_key not in systems:
                systems[system_key] = cs.key("us.electoral_system", system_key)
            contest = cs.key("us.contest", contest_key)
            previous = contests.get((state, office, district, party)) if runoff else None
            if runoff and previous is None:
                print(f"{contest_key}: a runoff without its first round")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind=general["kind"],
                office_id=general["office_id"],
                body_id=general["body_id"],
                seat_id=general["seat_id"],
                district_area_id=general["district_area_id"],
                boundary_set_id=general["boundary_set_id"],
                term=general["term"],
                magnitude=1,
                party_scope_id=cs.key("us.party", scope) if scope else None,
                round_number=2 if runoff else 1,
                previous_round_id=previous,
                electoral_system_id=systems[system_key],
                default_result_kind="counted",
            )
            if not runoff:
                contests[(state, office, district, party)] = contest
            where = (
                f"{STATES[state]}'s at-large congressional district"
                if district == 0
                else f"{STATES[state]}'s {district}{'th' if 10 <= district % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(district % 10, 'th')} congressional district"
                if office == "house"
                else STATES[state]
            )
            stage = (
                ("primary runoff" if runoff else "primary")
                if blanket
                else f"{shown} primary" + (" runoff" if runoff else "")
            )
            name(
                contest,
                f"2024 {STATES[state]} gubernatorial {stage}"
                if office == "governor"
                else f"2024 U.S. {'House' if office == 'house' else 'Senate special' if office == 'senate:special' else 'Senate'} {stage} in {where}",
            )
            path = (
                f"us/{state.lower()}/{day}-{kind}/"
                + (
                    "house-at-large"
                    if district == 0
                    else f"house-{district}"
                    if office == "house"
                    else office.replace(":", "-")
                )
                + (f"-{party}" if party else "")
            )
            if slugs.setdefault(path, contest) != contest:
                raise ValueError(f"slug {path} is taken")
            cs.add("slug", path=path, entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            total = lines["total"][0]
            counted = int(lines["counted"][0])
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
                completeness=1 if total is None else min(1, round(counted / total, 4)),
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            if total is not None and total > counted:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        f"The article's total line counts {total - counted:,} more votes than its candidate rows, "
                        "most likely blank ballots or scattered write-ins that it does not itemize."
                    ),
                )
            unit = general["unit"] if office == "house" and general["unit"] is not None else states[state]
            ranked: list[tuple[int, bool, UUID]] = []
            pooled = lines.with_columns(
                candidate=pl.when(pl.col("kind") == "write_in").then(pl.lit("Write-ins")).otherwise("candidate")
            )
            for candidate, kind_of, label, votes, winner in (
                pooled.group_by("candidate", "kind", maintain_order=True)
                .agg(pl.col("label").first(), pl.col("votes").sum(), pl.col("winner").any())
                .iter_rows()
            ):
                option_key = f"{contest_key}:{slug(candidate)}"
                option = cs.key("us.option", option_key)
                own = party_of(label or "") if kind_of == "candidate" else None
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind=kind_of,
                    party_id=cs.key("us.party", scope)
                    if scope and kind_of == "candidate"
                    else cs.key("us.party", own[1])
                    if own
                    else None,
                    ballot_label=candidate,
                )
                values.append((str(result_set), str(unit), str(option), int(votes or 0)))
                if kind_of != "candidate":
                    continue
                ranked.append((int(votes or 0), bool(winner), option))
                words = {word for word in slug(candidate).split("-") if word not in {"jr", "sr", "ii", "iii", "iv"}}
                surname = [word for word in slug(candidate).split("-") if word not in {"jr", "sr", "ii", "iii", "iv"}][
                    -1
                ]
                person = None
                if office != "governor":
                    hits = [
                        fec_id
                        for fec_id, filed in roster.get(
                            (state, "H" if office == "house" else "S", int(district or 0)), []
                        )
                        if surname in filed and (words - {surname}) & filed
                    ]
                    if len(set(hits)) == 1:
                        person = cs.key("fec.candidate", hits[0])
                        if hits[0] not in fec_known:
                            fec_known.add(hits[0])
                            cs.add("person", id=person)
                            name(person, candidate)
                if person is None:
                    people = [
                        entity
                        for text, entity in nominees[general_key]
                        if slug(text).split("-")[-1] == surname and (words - {surname}) & set(slug(text).split("-"))
                    ]
                    if len(set(people)) == 1:
                        person = people[0]
                if person is None:
                    person_key = f"{state}:{slug(candidate)}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    if person in cs.minted and person_key not in persons:
                        persons.add(person_key)
                        cs.add("person", id=person)
                        name(person, candidate)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{option_key}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=own is None and not scope,
                )
            ranked.sort(key=lambda entry: -entry[0])
            marked = [option for _, winner, option in ranked if winner]
            has_runoff = (
                not runoff
                and frame.filter(
                    pl.col("state") == state,
                    pl.col("office") == office,
                    pl.col("district").eq_missing(district),
                    pl.col("party").eq_missing(party),
                    pl.col("runoff"),
                ).height
                > 0
            )
            advancing = 4 if system_key == "top_four" else 2 if blanket or has_runoff else 1
            chosen = [option for _, _, option in ranked[:advancing]]
            if len(marked) == advancing and set(marked) != set(chosen):
                print(f"{contest_key}: the article marks other winners than the vote order, used its marks")
                chosen = marked
            elif len(ranked) > advancing and ranked[advancing - 1][0] == ranked[advancing][0]:
                print(f"{contest_key}: tied at the cut, no outcome")
                continue
            for option in chosen:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="advanced" if blanket or has_runoff else "won",
                )
        print(f"{len(contests)} primaries and {skipped} skipped")

        def split(text: str) -> tuple[str, str]:
            parts = [
                word
                for word in slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", text)).split("-")
                if word not in {"jr", "sr", "ii", "iii", "iv", ""}
            ] or ["", ""]
            return parts[0], parts[-1]

        def alike(a: str, b: str, initial: bool) -> bool:
            return a == b or a.startswith(b) or b.startswith(a) or initial and a[:2] == b[:2]

        presidential = pl.read_parquet(PARSED / "presidential.parquet")
        pool = [
            (*split(text), person, president)
            for person, text, president in conn.execute(
                """select cy.person_id, n.text, bool_or(x.value ~ ':president(:|$)')
                   from candidacy cy join contest_option o on o.id = cy.option_id
                   join contest c on c.id = o.contest_id join election e on e.id = c.election_id
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where e.date_from >= '2016-01-01' group by 1, 2"""
            ).fetchall()
        ]
        people: dict[str, UUID] = {}
        minted = dict(
            conn.execute("select value, entity_id from external_id where scheme = %s", [f"{SOURCE}.person"]).fetchall()
        )
        for candidate in presidential.filter(pl.col("named"))["candidate"].unique().sort():
            first, surname = split(candidate)
            words = {word for word in slug(candidate).split("-") if word not in {"jr", "sr", "ii", "iii", "iv"}}
            hits = sorted(
                {
                    fec_id
                    for fec_id, filed in roster[("US", "P", 0)]
                    if surname in filed and (words - {surname}) & filed
                },
                key=lambda fec_id: fec_id not in fec_known,
            )
            hit = hits[0] if hits and (len(hits) == 1 or hits[0] in fec_known and hits[1] not in fec_known) else None
            if hit is not None and hit in fec_known:
                people[candidate] = cs.key("fec.candidate", hit)
                continue
            found = next(
                (
                    group
                    for group in (
                        {
                            person
                            for given, family, person, held in pool
                            if family == surname and held == president and alike(first, given, president)
                        }
                        for president in (True, False)
                    )
                    if len(group) == 1
                ),
                None,
            )
            own = minted.get(f"US:{slug(candidate)}")
            others = {
                person
                for given, family, person, held in pool
                if family == surname and held and alike(first, given, True) and person != own
            }
            if own is not None and found in (None, {own}) and len(others) == 1:
                found = others
            if found is not None:
                people[candidate] = found.pop()
            elif hit is not None:
                fec_known.add(hit)
                people[candidate] = cs.key("fec.candidate", hit)
                cs.add("person", id=people[candidate])
                name(people[candidate], candidate)
        for candidate in presidential.filter(pl.col("named"))["candidate"].unique().sort():
            if candidate in people:
                continue
            first, surname = split(candidate)
            found = {
                person
                for other, person in people.items()
                if split(other)[1] == surname and alike(first, split(other)[0], True)
            }
            if len(found) == 1:
                people[candidate] = found.pop()
                continue
            people[candidate] = cs.key(f"{SOURCE}.person", f"US:{slug(candidate)}")
            cs.add("person", id=people[candidate])
            name(people[candidate], candidate)
        absorbed = {entity for entities in cs.merged.values() for entity in entities}
        for candidate, person in people.items():
            earlier = minted.get(f"US:{slug(candidate)}")
            if earlier is not None and earlier != person and str(earlier) not in absorbed:
                print("merging", candidate, earlier, "into", person)
                cs.merge(earlier, person)
        totals = {template: total for template, total in presidential.select("template", "total").unique().iter_rows()}
        nations: dict[str, UUID] = {}
        parents: dict[str, UUID] = {}
        for (party,), events in presidential.group_by("party"):
            first, last = events["day"].min(), events["day"].max()
            election_key = f"{first}:{party.lower()}-presidential-primaries"
            nation = parents[party] = cs.key("us.election", election_key)
            cs.add(
                "election",
                id=nation,
                polity_id=cs.polity("US"),
                kind="primary",
                date_from=first,
                date_to=last,
                status="held",
            )
            name(nation, f"2024 {party} Party presidential primaries")
            cs.add("slug", path=f"us/{first}-{party.lower()}-presidential-primaries", entity_id=nation)
            nations[party] = cs.key("us.contest", f"{first}:president:{party.lower()}")
            cs.add(
                "contest",
                id=nations[party],
                election_id=nation,
                kind="delegate_allocation",
                office_id=cs.key("us.office", "president"),
                party_scope_id=cs.key("us.party", f"{party.lower()}-party"),
                round_number=1,
                default_result_kind="counted",
            )
            name(nations[party], f"2024 {party} presidential nomination")
            cs.add(
                "slug", path=f"us/{first}-{party.lower()}-presidential-primaries/president", entity_id=nations[party]
            )
        named = STATES | {"LA": "Louisiana", "DC": "District of Columbia"}
        for (state, party, day, caucus, template), lines in presidential.sort("day", "state", "party").group_by(
            "state", "party", "day", "caucus", "template", maintain_order=True
        ):
            lower = party.lower()
            contest_key = f"{day}:{state}:president:{lower}"
            if contest_key in keys:
                print(f"{contest_key}: already held, skipped")
                for candidate in lines.filter(pl.col("kind") == "write_in", pl.col("named"))["candidate"].unique():
                    option_key = f"{contest_key}:{slug(candidate)}"
                    if conn.execute(
                        "select 1 from external_id where scheme = 'us.option' and value = %s", (option_key,)
                    ).fetchone():
                        cs.add(
                            "contest_option",
                            id=cs.key("us.option", option_key),
                            contest_id=cs.key("us.contest", contest_key),
                            kind="write_in",
                            party_id=None,
                            ballot_label=candidate,
                        )
                continue
            keys.add(contest_key)
            election_key = f"{day}:{state}:{lower}-caucuses" if caucus else f"{day}:{state}:primary"
            election = cs.key("us.election", election_key)
            if election_key not in elections:
                elections.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=parents[party] if caucus else None,
                    kind="caucus" if caucus else "primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    election,
                    f"2024 {named[state]} {party} caucuses" if caucus else f"2024 {named[state]} primary election",
                )
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{day}-{lower}-caucuses"
                    if caucus
                    else f"us/{state.lower()}/{day}-primary",
                    entity_id=election,
                )
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="delegate_allocation",
                office_id=cs.key("us.office", "president"),
                district_area_id=areas[state],
                party_scope_id=cs.key("us.party", f"{lower}-party"),
                allocation_parent_id=nations[party],
                round_number=1,
                default_result_kind="counted",
            )
            name(
                contest,
                f"2024 {named[state]} {party} caucuses"
                if caucus
                else f"2024 {named[state]} {party} presidential primary",
            )
            path = (
                f"us/{state.lower()}/{day}-{lower}-caucuses/president"
                if caucus
                else f"us/{state.lower()}/{day}-primary/president-{lower}"
            )
            if slugs.setdefault(path, contest) != contest:
                raise ValueError(f"slug {path} is taken")
            cs.add("slug", path=path, entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            counted = int(lines["votes"].sum())
            total = totals[template]
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
                completeness=1 if total is None else min(1, round(counted / total, 4)),
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=PRESIDENTIAL)
            if total is not None and total > counted:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        f"The article's total line counts {total - counted:,} more votes than its candidate rows, "
                        "most likely scattered write-ins that it does not itemize."
                    ),
                )
            for candidate, kind_of, personal, votes in (
                lines.group_by("candidate", "kind", "named", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                option_key = f"{contest_key}:{slug(candidate)}"
                option = cs.key("us.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind=kind_of,
                    party_id=cs.key("us.party", f"{lower}-party") if kind_of == "candidate" else None,
                    ballot_label=candidate,
                )
                values.append((str(result_set), str(states[state]), str(option), int(votes)))
                if not personal:
                    continue
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{option_key}:head"),
                    person_id=people[candidate],
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=False,
                )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
