import html
import re
import unicodedata
from pathlib import Path
from uuid import UUID

import httpx

from ea.store import Connection

PAGE = "https://www.votecnmi.gov.mp/general-election-2026.php"
CONTESTS = {
    "governor lt governor": "2026-11-03:MP:governor",
    "washington delegate": "2026-11-03:MP:house:0",
    "senate saipan": "2026-11-03:MP:69:senate:3rd-senatorial-district",
    "senate tinian": "2026-11-03:MP:69:senate:2nd-senatorial-district",
    "senate rota": "2026-11-03:MP:69:senate:1st-senatorial-district",
    "mayor saipan": "2026-11-03:MP:69:mayor:saipan",
    "mayor northern islands": "2026-11-03:MP:69:mayor:northern-islands",
    "mayor tinian": "2026-11-03:MP:69:mayor:tinian-and-aguiguan",
    "mayor rota": "2026-11-03:MP:69:mayor:rota",
    "saipan northern islands municipal council": "2026-11-03:MP:69:municipal-council:saipan",
    "tinian aguiguan municipal council": "2026-11-03:MP:69:municipal-council:tinian-and-aguiguan",
    "rota municipal council": "2026-11-03:MP:69:municipal-council:rota",
    "saipan northern islands board of education": "2026-11-03:MP:69:board-of-education:saipan",
    "attorney general": "2026-11-03:MP:69:attorney-general",
    "justice retention": "2026-11-03:MP:69:justice",
    "constitutional convention": "2026-11-03:MP:question:constitutional-convention-question",
}
HOUSE = re.compile(r"^house .*election district (\d)")
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "dr", "md", "phd", "esq"}


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    response = client.get(PAGE)
    if response.status_code != 200 or b"er-office__title" not in response.content:
        return {}
    return {PAGE: (response.content, ".html")}


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\bdlg\b", "deleon guerrero", re.sub(r'"[^"]*"', " ", text))
    return [w for w in re.findall(r"[a-z]+", text) if w not in SUFFIXES]


def key(title: str) -> str | None:
    plain = " ".join(re.findall(r"[a-z]+|\d+", html.unescape(title).lower()))
    house = HOUSE.match(plain)
    return f"2026-11-03:MP:69:house:district-{house[1]}" if house else CONTESTS.get(plain)


def read(path: Path) -> dict[str, list[tuple[str, int]]]:
    text = path.read_bytes().decode("utf-8", "replace")
    contests: dict[str, list[tuple[str, int]]] = {}
    for block in re.split(r'<div class="er-office" id="office-\d+">', text)[1:]:
        title = re.search(r'er-office__title">(.*?)</h2>', block, re.DOTALL)
        if title is None:
            continue
        rows = contests.setdefault(" ".join(html.unescape(title[1]).split()), [])
        table = re.search(r'<table class="er-retention-table.*?</table>', block, re.DOTALL)
        if table is not None:
            for row in re.findall(r"<tr.*?</tr>", table[0], re.DOTALL):
                cells = [
                    " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())
                    for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.DOTALL)
                ]
                if cells and cells[0] in ("YES", "NO"):
                    rows.append((cells[0].title(), int(cells[-1].replace(",", "") or 0)))
            continue
        for candidate in re.split(r'<div class="er-candidate[ "]', block)[1:]:
            name = re.search(r'er-candidate__name">(.*?)</span>', candidate, re.DOTALL)
            votes = re.search(r'er-candidate__votes">([\d,]+) votes?', candidate)
            if name is not None and votes is not None:
                rows.append((" ".join(html.unescape(name[1]).split()), int(votes[1].replace(",", ""))))
    return contests


def pick(name: str, labels: dict[UUID, list[str]]) -> UUID | None:
    if name in ("Yes", "No"):
        hits = [option for option, label in labels.items() if label == [name.lower()]]
        return hits[0] if len(hits) == 1 else None
    tokens = words(name.split("/")[0])
    if not tokens:
        return None
    hits = [option for option, label in labels.items() if label and label[-1] == tokens[-1]]
    if len(hits) > 1:
        hits = [option for option in hits if labels[option][0] == tokens[0]]
    if not hits:
        shared = {option: len(set(label) & set(tokens)) for option, label in labels.items()}
        best = max(shared.values(), default=0)
        hits = [option for option, count in shared.items() if count == best and count >= 2]
    return hits[0] if len(hits) == 1 else None


def plan(
    conn: Connection, paths: dict[str, Path]
) -> tuple[list[tuple[str, str, UUID, dict[tuple[UUID, UUID, int], int], tuple[int, int], str | None]], list[str]]:
    unit = conn.execute(
        """select u.id from reporting_unit u join polity_territory t on t.area_id = u.area_id
           join polity p on p.id = t.polity_id where p.iso_code = 'US-MP' and u.scheme_code = 'us.state'"""
    ).fetchall()[0][0]
    tallies = []
    unmatched = []
    for path in paths.values():
        for title, rows in read(path).items():
            contest_key = key(title)
            if contest_key is None:
                unmatched.append(f"{title}: no contest mapping")
                continue
            options = conn.execute(
                """select o.contest_id, o.id, coalesce(ca.ballot_name, o.ballot_label) from external_id e
                   join contest_option o on o.contest_id = e.entity_id and o.parent_option_id is null
                   left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where e.scheme = 'us.contest' and e.value = %s and o.kind <> 'write_in'""",
                [contest_key],
            ).fetchall()
            if not options:
                unmatched.append(f"no contest: {contest_key}")
                continue
            labels = {option: words(label or "") for _, option, label in options}
            sums: dict[tuple[UUID, UUID, int], int] = {}
            for name, votes in rows:
                option = pick(name, labels)
                if option is None:
                    unmatched.append(f"{contest_key}: {name}")
                    continue
                sums[(unit, option, 0)] = votes
            if sums:
                tallies.append(("MP", contest_key, options[0][0], sums, (0, 0), None))
    return tallies, unmatched
