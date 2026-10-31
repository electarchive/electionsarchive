import json
import re
import time
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.statewide_totals import INDEPENDENT, PARTIES
from ea.sources.us.wikipedia_primaries import clean, templates
from ea.sources.us.wikipedia_statewide import GENERIC, SKIP, STATES
from ea.store import DATA, connect

SOURCE = "us.wikipedia_gov_history"
VERSION = "3"
PARSED = DATA / "parsed" / f"{SOURCE}-3"
PARTIAL = {("NY", 1870), ("NY", 1896)}
SECTIONS = {
    ("AR", 1948): ("==General election==", "==References=="),
    ("LA", 1979): ("==Primary election==", "==Runoff election=="),
}
NO_OUTCOME = {("LA", 1979)}
INFOBOX = {("AR", 1990)}
API = "https://en.wikipedia.org/w/api.php?"
CHUNK = 20


def contests() -> list[tuple[str, int, str]]:
    with connect() as conn:
        return conn.execute(
            """select substr(p.iso_code, 4), extract(year from e.date_from)::int, c.id::text
               from contest c join election e on e.id = c.election_id join polity p on p.id = e.polity_id
               join external_id o on o.entity_id = c.office_id and o.scheme = 'us.office' and o.value like 'governor:%%'
               where e.date_from < '2000-01-01' and e.kind = 'general'
                 and exists (select from result_set_history h join result_set_drop d
                             on h.id = d.result_set_id and h.superseded_by = d.changeset_id where h.contest_id = c.id)
                 and not exists (select from result_set r join changeset k on k.id = r.changeset_id
                                 where r.contest_id = c.id and k.pipeline = %s)
                 and not exists (select from result_set r join changeset k on k.id = r.changeset_id
                                 where r.contest_id = c.id and r.is_default
                                   and coalesce(r.completeness, 1) >= 0.98 and k.pipeline not like 'us.nyc%%')
               order by 1, 2""",
            [SOURCE],
        ).fetchall()


def title(state: str, year: int) -> str:
    return f"{year} {STATES[state]} gubernatorial election"


def query(titles: list[str]) -> str:
    return API + urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content|ids",
            "rvslots": "main",
            "titles": "|".join(titles),
            "redirects": 1,
            "format": "json",
            "formatversion": 2,
        }
    )


def chunks() -> list[list[str]]:
    wanted = sorted({title(state, year) for state, year, _ in contests()})
    return [wanted[start : start + CHUNK] for start in range(0, len(wanted), CHUNK)]


def fetch() -> None:
    for group in chunks():
        url = query(group)
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, url, suffix=".json")
                break
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in (429, 503) or attempt == 5:
                    raise
                time.sleep(20 * 2**attempt)
        time.sleep(0.5)


def pages() -> dict[str, dict[str, str | int]]:
    found: dict[str, dict[str, str | int]] = {}
    for group in chunks():
        body = json.loads(raw.cached(SOURCE, query(group)).path.read_text())["query"]
        moved = {entry["from"]: entry["to"] for entry in body.get("redirects", [])}
        normal = {entry["from"]: entry["to"] for entry in body.get("normalized", [])}
        by_title = {entry["title"]: entry for entry in body["pages"] if "revisions" in entry}
        for asked in group:
            target = moved.get(normal.get(asked, asked), normal.get(asked, asked))
            if target in by_title:
                entry = by_title[target]
                found[asked] = {
                    "title": target,
                    "revid": entry["revisions"][0]["revid"],
                    "text": entry["revisions"][0]["slots"]["main"]["content"],
                }
    return found


SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    folded = re.sub(r"\([^)]*\)|\"[^\"]*\"", " ", folded)
    return [word for word in re.findall(r"[a-z]+", folded) if word not in SUFFIXES]


def strip(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*/\s*>|<ref(?:\s[^>]*[^/])?>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)


def label_of(text: str, start: int, params: dict[str, str]) -> str:
    own = clean(params.get("title", "")) or clean(next(iter(params.values()), ""))
    if own:
        return own
    heads = re.findall(r"^=+\s*(.*?)\s*=+\s*$", text[:start], flags=re.MULTILINE)
    return heads[-1] if heads else ""


def head(text: str) -> str:
    text = re.sub(
        r"\{\{\s*(?:ubl|unbulleted list|plainlist|flatlist|hlist)\s*\|\s*\*?\s*([^|{}\n]*)[^{}]*\}\}",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    return re.split(r"<br\s*/?>|\n", text, maxsplit=1)[0]


def cells(row: str) -> list[str]:
    found = []
    for line in row.split("\n"):
        line = line.strip()
        if line[:1] in ("|", "!") and line[:2] not in ("|-", "|+", "|}"):
            for part in re.split(r"\|\||!!", line[1:]):
                part = (
                    re.sub(r"^[^|\[{]*\|(?!\|)", "", part)
                    if re.match(r"^\s*(?:style|colspan|rowspan|align|bgcolor|width)[^|]*\|", part)
                    else part
                )
                found.append(clean(part.replace("&nbsp;", " ")))
    return found


def tables(text: str) -> list[tuple[str, int, list[dict[str, object]], str | None]]:
    boxes = []
    for begin in re.finditer(r"^\{\|", text, re.MULTILINE):
        end = text.find("\n|}", begin.start())
        body = text[begin.start() : end if end != -1 else len(text)]
        label = " ".join(re.findall(r"^=+\s*(.*?)\s*=+\s*$", text[: begin.start()], flags=re.MULTILINE)[-2:])
        if SKIP.search(label) or not re.search(r"candidate|votes", body[:1500], re.IGNORECASE):
            continue
        if re.search(r"lieutenant|ticket", body[:1500], re.IGNORECASE):
            continue
        mated = re.search(r"running mate", body[:1500], re.IGNORECASE) is not None
        rows: list[dict[str, object]] = []
        listed, total = 0, None
        for row in body.split("\n|-")[1:]:
            row_cells = cells(row)
            number = next(
                (
                    index
                    for index, cell in enumerate(row_cells)
                    if re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d{3,}", cell) and index > 0
                ),
                None,
            )
            if number is None:
                continue
            votes = int(row_cells[number].replace(",", ""))
            before = [cell for cell in row_cells[:number] if cell and re.search(r"[A-Za-z]", cell)]
            if before and re.match(r"total", before[0], re.IGNORECASE):
                total = votes
                continue
            if mated:
                if number < 3 or not row_cells[number - 2] or not row_cells[number - 3]:
                    continue
                before = [row_cells[number - 3], row_cells[number - 2]]
            if not before or re.match(r"majority|plurality|turnout|margin", before[-1], re.IGNORECASE):
                continue
            listed += votes
            percent = next((cell for cell in row_cells[number + 1 :] if "%" in cell), None)
            rows.append(
                {
                    "name": before[-1],
                    "party": before[-2] if len(before) > 1 else None,
                    "votes": votes,
                    "winner": 0,
                    "percentage": percent,
                }
            )
        if not rows:
            continue
        rows = fuse(rows)
        listed = sum(int(row["votes"]) for row in rows)
        reason = None
        if total is not None:
            if abs(listed - total) > max(5, total // 200):
                reason = f"rows sum {listed}, total line {total}"
        else:
            lead = max(rows, key=lambda row: row["votes"])
            percent = re.sub(r"[^\d.]", "", str(lead["percentage"] or ""))
            if not percent or abs(lead["votes"] * 100 / listed - float(percent)) > 1.5:
                reason = "no total line and percentages do not confirm the rows"
        boxes.append((label, listed, rows, reason))
    return boxes


def tickets(text: str) -> tuple[list[dict[str, object]], str | None]:
    for begin in re.finditer(r"^\{\|", text, re.MULTILINE):
        end = text.find("\n|}", begin.start())
        body = text[begin.start() : end if end != -1 else len(text)]
        if not re.search(r"^\|\+.*state election results", body, re.IGNORECASE | re.MULTILINE):
            continue
        parts = body.split("\n|-")
        labels = [
            re.sub(
                r"\s*tickets?\s*$", "", clean(re.sub(r"\{\{[^}]*\}\}", "", line.split("|", 2)[-1])), flags=re.IGNORECASE
            )
            for line in parts[1].split("\n")
            if line.startswith("! colspan")
        ]
        row = next(
            (part for part in parts[2:] if re.search(r"^\|\s*(?:\[\[Governor of.*|Governor)\s*$", part, re.MULTILINE)),
            None,
        )
        if row is None or not labels:
            continue
        found: list[str] = []
        for line in row.split("\n")[1:]:
            if line.startswith("|") and not line.startswith("|-"):
                found.append(
                    re.sub(r"^(?:\s*align=\"?\w+\"?\s*\|)?", "", re.sub(r"^\|(?:[^|]*\{\{[^}]*\}\}\s*\|)?", "", line))
                )
        pairs = list(zip(found[1::2], found[2::2], strict=False))
        merged: dict[str, dict[str, object]] = {}
        for label, (name, votes) in zip(labels, pairs, strict=False):
            digits = re.sub(r"\D", "", re.sub(r"\{\{efn.*", "", votes))
            person = clean(re.sub(r"\{\{efn.*", "", name).replace("'''", ""))
            if not person or not digits:
                continue
            if person in merged:
                merged[person]["votes"] = int(merged[person]["votes"]) + int(digits)
                continue
            merged[person] = {
                "name": person,
                "party": label,
                "votes": int(digits),
                "winner": 1 if "'''" in name else 0,
                "percentage": None,
            }
        rows = list(merged.values())
        if not rows:
            continue
        reason = None
        ranked = sorted((int(row["votes"]) for row in rows), reverse=True)
        for rank, number in enumerate(("1", "2")):
            printed = re.search(rf"popular_vote{number}\s*=\s*'*([\d,]+)", text)
            if printed is not None and int(printed[1].replace(",", "")) != ranked[rank]:
                reason = f"infobox shows {printed[1]} for rank {number}, table {ranked[rank]}"
        return rows, reason
    return [], None


def fuse(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    folded: dict[tuple[str, ...], dict[str, object]] = {}
    for row in rows:
        key = tuple(words(str(row["name"])))
        if key not in folded:
            folded[key] = dict(row)
            continue
        kept = folded[key]
        if str(row["party"]).lower() == "total":
            kept["votes"] = row["votes"]
        elif str(kept["party"]).lower() == "total":
            kept["party"] = row["party"]
        else:
            kept["votes"] = int(kept["votes"]) + int(row["votes"])
        kept["winner"] = max(int(kept["winner"]), int(row["winner"]))
        kept["percentage"] = None
    return list(folded.values())


def results_template(text: str) -> list[dict[str, object]]:
    begin = re.search(r"\{\{\s*Election results\s*\n", text, re.IGNORECASE)
    if begin is None:
        return []
    body = text[begin.end() : text.find("\n}}", begin.end())]
    rows: list[dict[str, object]] = []
    for number in range(1, 30):
        found = re.search(rf"cand{number}=(.*?)\|party{number}=(.*?)\|votes{number}=(\d+)", body)
        if found is None:
            break
        rows.append(
            {
                "name": clean(found[1].replace("'", "")),
                "party": clean(found[2]),
                "votes": int(found[3]),
                "winner": 1 if "'''" in found[1] else 0,
                "percentage": None,
            }
        )
    return rows


def infobox(text: str) -> tuple[list[dict[str, object]], str | None]:
    rows: list[dict[str, object]] = []
    for number in range(1, 10):
        votes = re.search(rf"popular_vote{number}\s*=\s*'*([\d,]+)", text)
        name = re.search(rf"nominee{number}\s*=\s*(.+)", text)
        if votes is None or name is None:
            break
        percent = re.search(rf"percentage{number}\s*=\s*'*([\d.]+)", text)
        party = re.search(rf"party{number}\s*=\s*(.+)", text)
        rows.append(
            {
                "name": clean(name[1].replace("'", "")),
                "party": clean(re.sub(r"\(.*?\)", "", party[1])) if party else None,
                "votes": int(votes[1].replace(",", "")),
                "winner": 1 if "'''" in name[1] else 0,
                "percentage": percent[1] if percent else None,
            }
        )
    listed = sum(int(row["votes"]) for row in rows)
    if any(
        row["percentage"] is None or abs(int(row["votes"]) * 100 / listed - float(str(row["percentage"]))) > 0.1
        for row in rows
    ):
        return rows, "infobox percentages do not confirm the rows"
    return rows, None


def read(text: str, skip: bool = True, infobox_only: bool = False) -> tuple[list[dict[str, object]], str | None]:
    boxes: list[tuple[str, int, list[dict[str, object]], str | None]] = []
    for begin in re.finditer(r"\{\{\s*election box begin", text, re.IGNORECASE):
        end = re.search(r"\{\{\s*election box end\s*\}\}", text[begin.start() :], re.IGNORECASE)
        body = text[begin.start() : begin.start() + end.end()] if end else text[begin.start() :]
        members = templates(body)
        label = label_of(text, begin.start(), members[0][1] if members else {})
        if skip and SKIP.search(label):
            continue
        rows: list[dict[str, object]] = []
        listed, total, turnout = 0, None, None
        for name, params in members[1:]:
            digits = re.sub(r"\D", "", clean(params.get("votes", "")))
            if name.startswith("election box turnout"):
                turnout = int(digits) if digits else None
            elif name.startswith("election box write-in") and "candidate" not in params and digits:
                listed += int(digits)
                rows.append({"name": "Others", "party": None, "votes": int(digits), "winner": 0, "percentage": None})
            elif name.startswith("election box total"):
                total = int(digits) if digits else None
            elif name.startswith("election box") and "candidate" in params and digits:
                listed += int(digits)
                candidate = clean(head(params["candidate"]))
                if not candidate:
                    continue
                party = re.sub(r"\(.*?\)", "", clean(params.get("party", ""))).replace("\u2013", "-").strip()
                rows.append(
                    {
                        "name": candidate,
                        "party": party or None,
                        "votes": int(digits),
                        "winner": 1 if "winning" in name else 0,
                        "percentage": params.get("percentage") or params.get("%"),
                    }
                )
        if not rows:
            continue
        rows = fuse(rows)
        listed = sum(int(row["votes"]) for row in rows)
        reason = None
        printed = [figure for figure in (total, turnout) if figure is not None]
        if any(abs(listed - figure) <= max(5, figure // 200) for figure in printed):
            pass
        elif total is not None:
            reason = f"rows sum {listed}, total line {total}"
        else:
            lead = max(rows, key=lambda row: row["votes"])
            percent = re.sub(r"[^\d.]", "", str(lead["percentage"] or ""))
            if not percent or abs(lead["votes"] * 100 / listed - float(percent)) > 1.5:
                reason = "no total line and percentages do not confirm the rows"
        boxes.append((label, listed, rows, reason))
    if not boxes:
        boxes = tables(text)
    if not boxes:
        rows, reason = tickets(text)
        if not rows:
            rows, reason = results_template(text), None
        if not rows and infobox_only:
            rows, reason = infobox(text)
        return (rows, reason) if rows else ([], "no election box")
    label, _, rows, reason = max(boxes, key=lambda box: box[1])
    return rows, reason


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    found = pages()
    targets = contests()
    with connect() as conn:
        options: dict[str, list[tuple[str, str, str | None, str | None, int, int | None]]] = {}
        for contest, option, kind, label, person, party, summed in conn.execute(
            """select c.id::text, o.id::text, o.kind::text, o.ballot_label, n.text, o.party_id::text,
                      (select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k
                       where r.contest_id = c.id and r.is_default and v.option_k = o.k and v.measure = 1)
               from contest c join contest_option o on o.contest_id = c.id
               left join candidacy cd on cd.option_id = o.id
               left join name n on n.entity_id = cd.person_id and n.is_preferred
               where c.id = any(%s::uuid[])""",
            [[contest for _, _, contest in targets]],
        ):
            options.setdefault(contest, []).append((option, kind, person or label, party, summed, None))
    lines: list[dict[str, object]] = []
    held: list[tuple[str, str]] = []
    disagree: list[tuple[str, str]] = []
    for state, year, contest in targets:
        asked = title(state, year)
        if asked not in found:
            held.append((f"{year} {state}", "no article"))
            continue
        article = strip(str(found[asked]["text"]))
        if (state, year) in SECTIONS:
            first, last = SECTIONS[(state, year)]
            article = re.sub(
                r"\{\{(?:efn|sfn)[^{}]*(?:\{\{[^{}]*\}\}[^{}]*)*\}\}",
                "",
                article[article.index(first) : article.index(last)],
            )
        rows, reason = read(article, skip=(state, year) not in SECTIONS, infobox_only=(state, year) in INFOBOX)
        if not rows:
            held.append((f"{year} {state}", reason or "no rows"))
            continue
        if (state, year) in NO_OUTCOME:
            for row in rows:
                row["winner"] = 0
        if sum(row["winner"] == 1 for row in rows) > 1:
            held.append((f"{year} {state}", "more than one winning row"))
            continue
        names = [str(row["name"]) for row in rows]
        if len({tuple(words(name)) for name in names}) != len(names):
            held.append((f"{year} {state}", "a name appears twice"))
            continue
        mine = options.get(contest, [])
        candidates = [option for option in mine if option[1] == "candidate"]
        matched: dict[int, str] = {}
        for index, row in enumerate(rows):
            parts = words(str(row["name"]))
            if GENERIC.match(str(row["name"])) or not parts:
                continue
            near = [
                option
                for option in candidates
                if (theirs := words(str(option[2])))
                and theirs[-1] == parts[-1]
                and (len(parts) == 1 or theirs[0][0] == parts[0][0])
            ]
            if len(near) == 1 and near[0][0] not in matched.values():
                matched[index] = near[0][0]
        offs = []
        for index, option in matched.items():
            theirs = next(o[4] for o in mine if o[0] == option)
            ours = int(rows[index]["votes"])
            if theirs and (theirs - ours) * 100 > ours:
                offs.append(f"{rows[index]['name']} {ours} vs {int(theirs)}")
        if reason is not None and (state, year) not in PARTIAL:
            held.append((f"{year} {state}", reason))
            continue
        if offs:
            disagree.append((f"{year} {state}", "; ".join(offs)))
        for index, row in enumerate(rows):
            generic = GENERIC.match(str(row["name"]))
            kind = "candidate"
            if generic:
                kind = "write_in" if str(row["name"]).lower().startswith("write") else "others"
            lines.append(
                {
                    "contest": contest,
                    "state": state,
                    "year": year,
                    "source": str(found[asked]["title"]),
                    "revid": int(found[asked]["revid"]),
                    "name": row["name"],
                    "party": row["party"],
                    "votes": row["votes"],
                    "winner": row["winner"],
                    "kind": kind,
                    "option": matched.get(index),
                    "caveat": "; ".join(offs) or None,
                }
            )
    frame = pl.DataFrame(
        lines,
        schema={
            "contest": pl.String,
            "state": pl.String,
            "year": pl.Int64,
            "source": pl.String,
            "revid": pl.Int64,
            "name": pl.String,
            "party": pl.String,
            "votes": pl.Int64,
            "winner": pl.Int64,
            "kind": pl.String,
            "option": pl.String,
            "caveat": pl.String,
        },
    )
    frame.write_parquet(PARSED / "lines.parquet")
    pl.DataFrame(held, schema=["subject", "reason"], orient="row").write_csv(PARSED / "held.tsv", separator="\t")
    pl.DataFrame(disagree, schema=["subject", "differences"], orient="row").write_csv(
        PARSED / "disagree.tsv", separator="\t"
    )
    print(
        f"{frame['contest'].n_unique()} contests, {len(held)} held back, {len(disagree)} exceed the printed totals,",
        f"{frame.filter(pl.col('option').is_not_null()).height} lines matched to existing options of {frame.height}",
    )


CAVEAT = "Some candidates' figures printed in the article are lower than the partial returns the archive holds for the same contest ({offs}); an official canvass will settle which is right."
TITLE = "Gubernatorial election articles, general election results"
NOTE = (
    "Statewide totals from the election box or results table in the English Wikipedia's article on this "
    "gubernatorial election, read from the article revision cited in the source. Wikipedia ranks below an official "
    "canvass, which will supersede this set."
)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    urls = {asked: query(group) for group in chunks() for asked in group}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governor general election totals by state, 1865 to 1999, from the English Wikipedia's articles",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            parts = words(text)
            return parts[-1] if parts else ""

        state_units = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                """select substr(p.iso_code, 4), u.code, u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        contests = {
            str(contest): (election, key)
            for contest, election, key in conn.execute(
                """select c.id, c.election_id, min(k.value) from contest c
                   join external_id k on k.entity_id = c.id and k.scheme = 'us.contest'
                   where c.id = any(%s::uuid[]) group by 1, 2""",
                [frame["contest"].unique().to_list()],
            ).fetchall()
        }
        priors: dict[str, list[tuple]] = {}
        columns = (
            "id",
            "election_id",
            "contest_id",
            "stage",
            "kind",
            "status",
            "source_id",
            "as_of",
            "completeness",
            "supersedes_id",
        )
        for row in conn.execute(
            f"select {', '.join(columns)} from result_set where contest_id = any(%s::uuid[]) and is_default",
            [frame["contest"].unique().to_list()],
        ).fetchall():
            priors.setdefault(str(row[2]), []).append(row)
        pools: dict[str, dict[str, list[str]]] = {}
        for contest, kind, option in conn.execute(
            """select contest_id::text, kind::text, id::text from contest_option
               where contest_id = any(%s::uuid[]) and kind in ('write_in', 'others') order by id""",
            [frame["contest"].unique().to_list()],
        ).fetchall():
            pools.setdefault(contest, {}).setdefault(kind, []).append(option)
        deaths: dict[UUID, date] = dict(
            conn.execute("select id, death_date from person where death_date is not null").fetchall()
        )
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        fptp = cs.key("us.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("the electoral system must already exist")
        people: dict[str, dict[str, list[tuple[UUID, int]]]] = {}
        minted: set[UUID] = set()
        sources_made: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}

        def party(state: str, label: str | None) -> tuple[UUID | None, bool]:
            if label is None or not label.strip():
                return None, False
            if label.lower() in INDEPENDENT or re.match(
                r"independent\b(?!.*(?:republican|democrat))|nonpartisan", label, re.IGNORECASE
            ):
                return None, True
            if re.search(r"[/&]|\band\b|\bfusion\b", label, re.IGNORECASE):
                return None, False
            if (national := PARTIES.get(label.lower().removesuffix(" party"))) is not None:
                return cs.key("us.party", national), False
            text = label if re.search(r"\bparty\b", label, re.IGNORECASE) else f"{label} Party"
            options = [
                f"{state}:{slug(text)}",
                f"{state}:{slug(text.removesuffix(' Party'))}",
                slug(text),
                slug(text.removesuffix(" Party")),
            ]
            key = next((key for key in options if key in parties_known), options[0])
            found = cs.key("us.party", key)
            if key not in parties_known:
                parties_known.add(key)
                cs.add("party", id=found, kind="party", polity_id=cs.polity(f"US-{state}"))
                name(found, text)
            return found, False

        filled = 0
        for (contest_id,), lines in frame.sort("state", "year", "contest").group_by("contest", maintain_order=True):
            election, ckey = contests[str(contest_id)]
            state = str(lines["state"][0])
            year = int(lines["year"][0])
            unit_code, area = state_units[state]
            asked = title(state, year)
            article = str(lines["source"][0])
            if asked not in sources_made:
                made = cs.source("Wikipedia", article)
                sources_made[asked] = made
                if made in cs.minted:
                    cs.add(
                        "source",
                        id=made,
                        kind="community",
                        publisher="Wikipedia",
                        title=article,
                        url=f"https://en.wikipedia.org/wiki/{quote(article.replace(' ', '_'))}",
                        license="CC BY-SA 4.0",
                    )
                artifacts[asked] = cs.artifact(raw.cached(SOURCE, urls[asked]), made)
            if state not in people:
                catalog: dict[str, list[tuple[UUID, int]]] = {}
                for person, text, seen in conn.execute(
                    """select distinct cd.person_id, n.text, extract(year from e.date_from)::int from candidacy cd
                       join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
                       join election e on e.id = c.election_id
                       join name n on n.entity_id = cd.person_id and n.is_preferred
                       where c.district_area_id = %s""",
                    [area],
                ).fetchall():
                    if text.split():
                        catalog.setdefault(f"{plain(text.split()[0])}|{surname(text)}", []).append((person, seen))
                people[state] = catalog
            result_set = cs.key(f"{SOURCE}.result_set", ckey)
            for prior in priors.get(str(contest_id), []):
                cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=UUID(str(contest_id)),
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources_made[asked],
                is_default=True,
                completeness=1,
                supersedes_id=None,
            )
            if (caveat := lines["caveat"][0]) is not None:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEAT.format(offs=caveat))
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=f"{NOTE} Article revision {int(lines['revid'][0])}.",
            )
            totals: dict[UUID, int] = {}
            declared: UUID | None = None
            spare = {kind: list(options) for kind, options in pools.get(str(contest_id), {}).items()}
            for candidate, party_label, votes, winner, kind, held in (
                lines.sort("votes", descending=True)
                .select("name", "party", "votes", "winner", "kind", "option")
                .iter_rows()
            ):
                if held is not None:
                    option = UUID(held)
                elif kind != "candidate":
                    if spare.get(kind):
                        option = UUID(spare[kind].pop(0))
                    else:
                        option = cs.key("us.option", f"{ckey}:wp-{kind}-{slug(candidate)}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=UUID(str(contest_id)),
                            kind=kind,
                            ballot_label="Write-ins" if kind == "write_in" else "Others",
                        )
                else:
                    on = date(year, 11, 1)
                    probe = f"{plain(candidate.split()[0])}|{surname(candidate)}"
                    found = {
                        person
                        for person, seen in people[state].get(probe, [])
                        if abs(seen - year) <= 16 and deaths.get(person, on) >= on
                    }
                    person = (
                        next(iter(found))
                        if len(found) == 1
                        else cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}:{year}")
                    )
                    if person in cs.minted and person not in minted:
                        minted.add(person)
                        cs.add("person", id=person)
                        name(person, candidate)
                        people[state].setdefault(probe, []).append((person, year))
                    label, independent = party(state, party_label)
                    option = cs.key("us.option", f"{ckey}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=UUID(str(contest_id)),
                        kind="candidate",
                        party_id=label,
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{ckey}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=independent,
                    )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", unit_code),
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
                if kind == "candidate":
                    totals[option] = votes
                    if winner == 1:
                        declared = option
            if (state, year) not in NO_OUTCOME:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=declared if declared is not None else max(totals, key=totals.__getitem__),
                    kind="elected",
                    seats=1,
                )
            filled += 1
        print(filled, "contests")
        return cs.write()
