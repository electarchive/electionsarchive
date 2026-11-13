import json
import re
import subprocess
import unicodedata
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.licenses import PUBLIC_RECORD
from ea.store import connect

SOURCE = "ch.bundesrat"
VERSION = "1"
PDF = "https://www.parlament.ch/centers/documents/de/wa-br-wahlresultate.pdf"
PDF_TITLE = "Resultate der Wahlen des Bundesrats, der Bundeskanzler und des Generals"
PUBLISHER = "Parlamentsdienste der Schweizerischen Bundesversammlung"
VOTES, CAST, VALID, INVALID, BLANK, PRESENT = 1, 3, 4, 5, 6, 20
DATE = re.compile(r"\d\d\.\d\d\.\d{4}")
REPLACED = re.compile(r"(?:Herrn?|Frau) ([A-ZÄÖÜÉÈ][A-ZÄÖÜÉÈ\-']+(?: [A-ZÄÖÜÉÈ\-']+)*)(?!\w)")
LOOSE = re.compile(r"\d\d\.\d\d[. ]\d{4}")
NUMBER = re.compile(r"(?<![\w.])\d+(?![\w.])")
PAIR = re.compile(r"([A-Za-zÀ-ÿ'’\-. ]+?(?: \([^)]*\))?)\s+(\d+)(?=\s|$)")
WINNER = re.compile(r"[A-ZÄÖÜÉÈ](?:[A-ZÄÖÜÉÈ\-']| (?! ))*[A-ZÄÖÜÉÈ]")
LABELS = {
    "ausgeteilt": "present",
    "eingelangt": "cast",
    "leer": "blank",
    "ungültig": "invalid",
    "gültig": "valid",
    "absolutes Mehr": "required",
}
SPELLINGS = {"PILEZ-GOLAZ": "PILET-GOLAZ"}
NAMES = {
    "Karin Keller Sutter": "Karin Keller-Sutter",
    "Johann Schneider-Ammann": "Johann N. Schneider-Ammann",
}
DECLINED = {("03.03.1993", "MATTHEY"): "Matthey Francis"}
MORE_FIXES = {("11.03.1999", "DEISS", 2): 8, ("11.03.1999", "DEISS", 3): 12}
SYSTEM_NOTE = (
    "The United Federal Assembly, the National Council and the Council of States sitting together, elects each member "
    "of the Federal Council by secret ballot with the absolute majority of the valid votes, which excludes blank and "
    "invalid ballots. Rounds are repeated until a candidate reaches it; the candidate with the fewest votes may be "
    "dropped from later rounds. Every member is elected in a separate vote for one seat, and a general renewal is a "
    "series of such votes in a fixed order."
)
SOURCE_NOTE = (
    "Counts are transcribed from the Parliamentary Services' record of the results. Each ballot's votes for "
    "candidates and scattered votes were checked against the valid votes it prints."
)
BLANK_NOTE = (
    "Blank ballots are stored as blank, and the ballots distributed as the members present. The first ballot of a "
    "seat is the contest; each later ballot has the earlier one as its previous round."
)


def iso_text(text: str | None) -> date | None:
    return None if text is None else day(text)


def day(text: str) -> date:
    return date(*map(int, reversed(text.split("."))))


def number(text: str) -> int:
    return int(text.replace(".", ""))


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold().replace("-", " ")


SPARQL = "https://query.wikidata.org/sparql?"
HOLDERS = SPARQL + urlencode(
    {
        "query": "SELECT ?p ?l ?b ?d ?start ?end WHERE { ?p p:P39 ?st . ?st ps:P39 wd:Q11811941 . "
        "OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end } ?p rdfs:label ?l FILTER(lang(?l) = 'de') "
        "OPTIONAL { ?p wdt:P569 ?b } OPTIONAL { ?p wdt:P570 ?d } }",
        "format": "json",
    }
)
POLITICIANS = SPARQL + urlencode(
    {
        "query": "SELECT ?p ?l ?b WHERE { ?p wdt:P27 wd:Q39 ; wdt:P106 wd:Q82955 . FILTER EXISTS { ?p wdt:P39 [] } "
        "?p rdfs:label ?l FILTER(lang(?l) = 'de') OPTIONAL { ?p wdt:P569 ?b } }",
        "format": "json",
    }
)
WIKIDATA_TITLE = "Members of the Swiss Federal Council (P39 Q11811941) and Swiss politicians"
LIST_ALIASES = {"Wahlen Fritz Traugott": "Wahlen Friedrich Traugott", "Kläti": "Klöti", "Koechelin": "Koechlin"}
SURNAMES = {"Brunschwig Graf Martine": "Martine Brunschwig Graf"}
PICKS = {("05.12.1973", "Schmid"): "Schmid Arthur", ("01.12.1989", "Weber"): "Weber Monika"}


def fetch() -> None:
    raw.fetch(SOURCE, PDF, suffix=".pdf")
    for url in (HOLDERS, POLITICIANS):
        raw.fetch(SOURCE, url, suffix=".json")
    fetch_pages()


def wikidata_rows(url: str) -> list[dict[str, str]]:
    bindings = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]
    return [{key: value["value"] for key, value in row.items()} for row in bindings]


def iso(text: str | None) -> date | None:
    return date.fromisoformat(text[:10]) if text and text[:1].isdigit() else None


def candidate_names(lines: list[str]) -> list[str]:
    start = next(i for i, line in enumerate(lines) if "Wahl des Bundesrates ab 1919 - Stimmen haben erhalten" in line)
    end = next(
        i for i, line in enumerate(lines) if i > start and line.strip() == "Mitglieder des Bundesrates seit 1848"
    )
    out: list[str] = []
    for line in lines[start + 1 : end]:
        row = re.match(r"(\S.*?)(?:\s{2,}|$)", line)
        if row and not row[1].startswith(("Nom", "Name", "*)")):
            name = re.sub(r"\s*\(\d+\s*\)", "", row[1]).strip()
            if name not in out:
                out.append(name)
    return out


def numbers(line: str, after: int) -> list[tuple[int, int]]:
    return [(m.end(), int(m.group())) for m in NUMBER.finditer(" " * after + line[after:])]


def pdf_lines() -> list[str]:
    path = raw.cached(SOURCE, PDF).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return [line.replace("\f", "").replace(" ", " ").rstrip() for line in out.split("\n")]


def blocks(lines: list[str]) -> list[list[str]]:
    start = next(i for i, line in enumerate(lines) if line.strip() == "Wahl des Bundesrats")
    end = next(i for i, line in enumerate(lines) if line.strip() == "Wahl des Bundeskanzlers" and i > start)
    kept = [
        line
        for line in lines[start:end]
        if line.strip() and not re.fullmatch(r"\s*-\s*\d+\s*-\s*", line) and line.strip() != "Wahl des Bundesrats"
    ]
    out: list[list[str]] = []
    done = True
    for line in kept:
        if done and (DATE.search(line) or re.match(r"\s+(Ersatz|Erste)", line)):
            out.append([])
            done = False
        out[-1].append(line)
        done = done or line.startswith("weitere Stimmen")
    return out


def members(lines: list[str]) -> list[dict[str, Any]]:
    start = next(i for i, line in enumerate(lines) if line.strip() == "Mitglieder des Bundesrates seit 1848")
    end = next(i for i, line in enumerate(lines) if i > start and line.startswith("Bundespräsidenten seit 1848"))
    out = []
    for line in lines[start:end]:
        found = [re.sub(r"(\d\d\.\d\d)[. ](\d{4})", r"\1.\2", d) for d in LOOSE.findall(line)]
        row = re.match(r"(\S.*?)\s{2,}\d\d\.\d\d[. ]\d{4}", line)
        if row and len(found) >= 2:
            out.append(
                {
                    "name": row[1].replace("MarceI", "Marcel"),
                    "born": found[0],
                    "elected": found[1],
                    "left": found[2] if len(found) > 2 else None,
                    "died": found[3] if len(found) > 3 else None,
                }
            )
    return out


def read_block(block: list[str]) -> list[dict[str, Any]]:
    rows: dict[str, list[tuple[int, int]]] = {}
    names: list[tuple[int, str, int]] = []
    winners: list[str] = []
    dates: list[tuple[int, str]] = []
    replaced: list[tuple[int, str]] = []
    mode = ""
    for line in block:
        stripped = line.strip()
        label = re.sub(r"^•\s*", "", stripped)
        found = next(
            (key for key in LABELS if label.startswith(key) and (stripped[0] == "•" or key == "absolutes Mehr")), None
        )
        if found:
            rows[LABELS[found]] = numbers(line, line.index(found) + len(found))
            mode = ""
        elif stripped.startswith("gewählt ist"):
            winners = [
                SPELLINGS.get(m.group().strip(), m.group().strip())
                for m in WINNER.finditer(re.sub(r"^gewählt ist (Herr/Frau|Herr|Frau)", lambda x: " " * len(x[0]), line))
            ]
            mode = ""
        elif stripped.startswith("mit ... Stimmen"):
            rows["winner"] = numbers(line, len("mit ... Stimmen"))
        elif stripped.startswith("weitere Stimmen"):
            rows["more"] = numbers(line, len("weitere Stimmen"))
            mode = ""
        elif re.match(r"Stimmen haben|erhalten", stripped) or mode == "names":
            mode = "names"
            cleaned = re.sub(r"^(\s*)(Stimmen haben erhalten|Stimmen haben|erhalten)", lambda m: " " * len(m[0]), line)
            names += [(m.end(), m[1].strip(), int(m[2])) for m in PAIR.finditer(cleaned)]
        else:
            dates += [(m.start(), m[0]) for m in DATE.finditer(line)]
            replaced += [(m.start(), m[1]) for m in REPLACED.finditer(line)]
    ends = [end for end, _ in rows["present"]]

    def column(end: int) -> int:
        return min(range(len(ends)), key=lambda k: abs(ends[k] - end))

    cols: list[dict[str, Any]] = [{"names": []} for _ in ends]
    for key, values in rows.items():
        for end, value in values:
            cols[column(end)][key] = value
    for end, name, votes in names:
        cols[column(end)]["names"].append((name, votes))
    named = iter(winners)
    for k, col in enumerate(cols):
        if "winner" in col:
            col["winner_name"] = next(named)
        col["end"] = ends[k]
    dates.sort()
    replaced.sort()
    for col in cols:
        col["date"] = [d for x, d in dates if x <= col["end"]][-1]
        before = [name for x, name in replaced if x <= col["end"]]
        col["replaced"] = before[-1] if before else None
    seats: list[dict[str, Any]] = []
    for col in cols:
        if not seats or "winner" in seats[-1]["rounds"][-1] or col["date"] != seats[-1]["date"]:
            seats.append({"date": col["date"], "replaced": col["replaced"], "rounds": []})
        seats[-1]["rounds"].append(col)
    return seats


def parse() -> list[dict[str, Any]]:
    lines = pdf_lines()
    people = members(lines)
    out: list[dict[str, Any]] = []
    for block in blocks(lines):
        out += read_block(block)
    for seat in out:
        for index, col in enumerate(seat["rounds"], 1):
            fix = MORE_FIXES.get((seat["date"], seat["rounds"][-1]["winner_name"], index))
            if fix is not None:
                col["more"] = fix
    for seat in out:
        seat["day"] = day(seat["date"])
        winner = seat["rounds"][-1].get("winner_name")
        if winner is None:
            raise ValueError(f"seat of {seat['date']} has no winner")
        match: list[dict[str, Any]] = [
            p
            for p in people
            if fold(p["name"]).startswith(fold(winner) + " ")
            and (day(p["elected"]) - seat["day"]).days <= 14
            and (p["left"] is None or day(p["left"]) >= seat["day"])
        ]
        if not match and (seat["date"], winner) in DECLINED:
            match = [{"name": DECLINED[seat["date"], winner], "born": None, "elected": seat["date"], "left": None}]
        if len(match) != 1:
            raise ValueError(f"{seat['date']} {winner}: {len(match)} members")
        seat["member"] = match[0]
        seat["surname"] = match[0]["name"][: len(winner)]
        seat["given"] = match[0]["name"][len(winner) :].strip()
    return out


BASE = "https://www.parlament.ch/de/"
ARCHIVE = BASE + "%C3%BCber-das-parlament/archiv/wahlen-im-rueckblick/bundesratswahlen/"
PAGES = {
    date(2006, 6, 14): ARCHIVE + "ersatzwahl-deiss",
    date(
        2007, 12, 12
    ): "https://www.parlament.ch/fr/%C3%BCber-das-parlament/archive/retrospective-des-elections/election-conseil-federal/2007-12-12",
    date(2008, 12, 10): ARCHIVE + "ersatzwahl-schmid",
    date(2009, 9, 16): ARCHIVE + "ersatzwahl-couchepin",
    date(2010, 9, 22): ARCHIVE + "ersatzwahl-leuenberger-merz",
    date(2011, 12, 14): ARCHIVE + "2011-12-14",
    date(2015, 12, 9): ARCHIVE + "2015-12-09",
    date(2017, 9, 20): ARCHIVE + "ersatzwahl-burkhalter-didier",
    date(2019, 12, 11): ARCHIVE + "2019-12-11",
    date(2023, 12, 13): BASE + "ratsbetrieb/wahlen/gesamterneuerungswahl-vom-13-dezember-2023",
    date(2025, 3, 12): BASE + "ratsbetrieb/wahlen/ersatzwahl-br-amherd",
}
HTML_TITLE = "Bundesratswahlen: Wahlen im Rückblick"
BULLETIN = "https://ws.parlament.ch/odata.svc/Transcript"
BULLETIN_TITLE = "Amtliches Bulletin: Vereinigte Bundesversammlung"
BULLETIN_DAYS = {
    date(2018, 12, 5): {"Amherd Viola": "LEUTHARD", "Keller-Sutter Karin": "SCHNEIDER-AMMANN"},
    date(2022, 12, 7): {"Rösti Albert": "MAURER", "Baume-Schneider Elisabeth": "SOMMARUGA"},
}
RESULT = "Ergebnis der Wahl - Résultat du scrutin"
BULLETIN_COUNTS = {
    "present": r"Ausgeteilte Wahlzettel - Bulletins délivrés",
    "cast": r"eingelangt - rentrés",
    "blank": r"leer - blancs",
    "invalid": r"ungültig - nuls",
    "valid": r"gültig - valables",
    "required": r"absolutes Mehr - Majorité absolue",
}
ELECTED = re.compile(r"Es wird gewählt - Est élue?\s*(\S.*?) \.\.\. mit (\d+) Stimmen")
LISTED = re.compile(r"(\S.*?) \.\.\. (\d+)")
COUNTS = {
    "Ausgeteilte Wahlzettel": "present",
    "Eingegangene Wahlzettel": "cast",
    "Leer": "blank",
    "Ungültig": "invalid",
    "Gültig": "valid",
    "Absolutes Mehr": "required",
    "Bulletins délivrés": "present",
    "Bulletins rentrés": "cast",
    "Blanc": "blank",
    "Blancs": "blank",
    "Nul": "invalid",
    "Nuls": "invalid",
    "Valables": "valid",
    "Majorité absolue": "required",
}
OTHERS = {"Verschiedene", "Divers"}
NAMES_HEAD = re.compile(r"^\|?\s*(Stimmen haben erhalten|Ont obtenu des voix)")
WON = re.compile(r"^(Gewählt ist|Est élue?)\s*:?\s*\|?\s*(.*?)\s*\|?$")


def bulletin_url(day: date) -> str:
    return (
        f"{BULLETIN}?$filter=MeetingDate eq '{day:%Y%m%d}' and Language eq 'DE' and MeetingCouncilAbbreviation eq 'V'"
        "&$select=ID,SortOrder,Text&$orderby=SortOrder&$top=400&$format=json"
    )


def fetch_pages() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for day_ in BULLETIN_DAYS:
        raw.fetch(SOURCE, bulletin_url(day_), suffix=".json")


def flatten(html: str) -> list[str]:
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.DOTALL)
    html = html[html.index("detail-content") :]
    html = re.sub(r"</(td|th)>", " | ", html)
    html = re.sub(r"</(tr|p|h\d|div|li)>", "\n", html)
    html = re.sub(r"<br\s*/?>", " ", html)
    text = unescape(re.sub(r"<[^>]+>", "", html)).replace(" ", " ").replace("\u200b", "")
    return [re.sub(r"\s+", " ", line).strip() for line in text.split("\n") if line.strip()]


def cells(line: str) -> list[str]:
    parts = [part.replace("/", " ").strip() for part in line.split("|")]
    while parts and not parts[-1]:
        parts.pop()
    return [NAMES.get(part, part) for part in (re.sub(r"\s+", " ", part) for part in parts)]


def count(cell: str) -> int | None:
    digits = re.sub(r"\D", "", cell)
    return int(digits) if digits else None


ROUND = re.compile(r"(\d+)(?:\.|er|e)? ?(?:Wahlgang|tour)")


def rounds_of(line: str) -> list[int]:
    return [int(n) for n in ROUND.findall(line)]


def winner_near(lines: list[str], head: int, end: int) -> str | None:
    for lo, hi in ((head, end + 2), (max(head - 6, 0), head)):
        window = lines[lo:hi]
        for w, line in enumerate(window):
            found = WON.match(line)
            if found and re.search(r"[A-Za-zÀ-ÿ]", found[2]):
                return found[2]
            if found:
                for later in window[w + 1 :]:
                    first = cells(later)[0] if cells(later) else ""
                    if re.search(r"[A-Za-zÀ-ÿ]", first) and not NAMES_HEAD.match(later):
                        return first
            if "Name, Vorname" in line and w + 1 < len(window):
                row = re.match(r"^([^|,]+), ([^|]+?) \|", window[w + 1])
                if row:
                    return f"{row[2]} {row[1]}"
    return None


def read_page(lines: list[str], day: date) -> list[dict[str, Any]]:
    tables: list[tuple[int, list[int], dict[str, list[int | None]]]] = []
    for at, line in enumerate(lines):
        if NAMES_HEAD.match(line) or not rounds_of(line):
            continue
        counts: dict[str, list[int | None]] = {}
        for later in lines[at + 1 : at + 9]:
            row = cells(later)
            if not row or row[0] not in COUNTS:
                break
            counts[COUNTS[row[0]]] = [count(c) for c in row[1:]]
        if counts:
            tables.append((at, rounds_of(line), counts))
    seats: list[dict[str, Any]] = []
    for head, line in enumerate(lines):
        if not NAMES_HEAD.match(line):
            continue
        start = head if rounds_of(line) else head + 1
        numbers_ = rounds_of(lines[start])
        names: list[tuple[str, list[int | None]]] = []
        k = start + 1
        while k < len(lines) and "|" in lines[k] and not NAMES_HEAD.match(lines[k]) and not rounds_of(lines[k]):
            row = cells(lines[k])
            if len(row) == 1 and k + 1 < len(lines) and re.match(r"^[\d\s|/]+$", lines[k + 1]):
                k += 1
                row += cells(lines[k])
            if not row or count(row[0]) is not None:
                break
            names.append((row[0], [count(c) for c in row[1:]] + [None] * len(numbers_)))
            k += 1
        before = [t for t in tables if t[0] < head and t[1] == numbers_]
        counts = before[-1][2] if before else {}
        rounds = []
        for r in range(len(numbers_)):
            col: dict[str, Any] = {"names": [], "more": None}
            for key, values in counts.items():
                if r < len(values) and values[r] is not None:
                    col[key] = values[r]
            for label, votes in names:
                if votes[r] is None:
                    continue
                if label in OTHERS:
                    col["more"] = votes[r]
                else:
                    col["names"].append((label, votes[r]))
            rounds.append(col)
        winner = winner_near(lines, head, k)
        winner = NAMES.get(winner, winner) if winner else None
        if numbers_[0] > 1 and seats:
            seats[-1]["rounds"] += rounds
            seats[-1]["winner"] = seats[-1]["winner"] or winner
        else:
            seats.append({"day": day, "date": f"{day:%d.%m.%Y}", "winner": winner, "replaced": None, "rounds": rounds})
    for seat in seats:
        if seat["winner"] is None:
            raise ValueError(f"{day}: seat without a winner")
    return seats


def parse_pages() -> list[dict[str, Any]]:
    out = []
    for day, url in PAGES.items():
        found = read_page(flatten(raw.cached(SOURCE, url).path.read_text()), day)
        out += found
    return out


def read_bulletin(day_: date) -> list[dict[str, Any]]:
    entries = json.loads(raw.cached(SOURCE, bulletin_url(day_)).path.read_text())["d"]
    entries = entries["results"] if isinstance(entries, dict) else entries
    body = " ".join(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", entry["Text"])) for entry in entries)
    body = re.sub(r"\[PAGE \d+\]|\[GZ\]|\[NB\]", " ", body)
    body = re.sub(r"\s+", " ", body)
    seats: list[dict[str, Any]] = []
    rounds: list[dict[str, Any]] = []
    for chunk in body.split(RESULT)[1:]:
        col: dict[str, Any] = {"names": [], "more": None}
        for key, label in BULLETIN_COUNTS.items():
            found = re.search(label + r" \.\.\. (\d+)", chunk)
            if found:
                col[key] = int(found[1])
        elected = ELECTED.search(chunk)
        if elected:
            col["winner"] = int(elected[2])
        heading = re.search(
            r"Ferner haben Stimmen erhalten-Ont en outre obtenu des voix|Stimmen haben erhalten - Ont obtenu des voix",
            chunk,
        )
        if heading:
            region = chunk[heading.end() :].split("[VS]")[0]
            for label, votes in LISTED.findall(region):
                if label in OTHERS:
                    col["more"] = int(votes)
                else:
                    col["names"].append((label.strip(), int(votes)))
        rounds.append(col)
        if elected:
            winner = re.sub(r"\s+", " ", elected[1]).strip()
            if winner in BULLETIN_DAYS[day_]:
                surname, given = winner.split(" ", 1)
                seats.append(
                    {
                        "day": day_,
                        "date": f"{day_:%d.%m.%Y}",
                        "winner": winner,
                        "surname": winner,
                        "full": f"{given} {surname}",
                        "replaced": BULLETIN_DAYS[day_][winner],
                        "rounds": rounds,
                    }
                )
            rounds = []
    if [seat["winner"] for seat in seats] != list(BULLETIN_DAYS[day_]):
        raise ValueError(f"{day_}: expected {list(BULLETIN_DAYS[day_])}, found {[seat['winner'] for seat in seats]}")
    return seats


def caveat(rounds: list[dict[str, Any]], index: int) -> str | None:
    col = rounds[index]
    counted = col.get("winner", 0) + sum(v for _, v in col["names"]) + (col.get("more") or 0)
    notes = []
    if "valid" in col and counted != col["valid"]:
        notes.append(
            f"the votes for candidates and the scattered votes add up to {counted:,} against {col['valid']:,} valid votes"
        )
    gap = col.get("blank", 0) + col.get("invalid", 0) + col.get("valid", 0)
    if "cast" in col and "valid" in col and gap != col["cast"]:
        notes.append(
            f"the blank, invalid, and valid ballots add up to {gap:,} against {col['cast']:,} ballots received"
        )
    if not notes:
        return None
    return "The printed figures do not agree: " + ", and ".join(notes) + ". All figures are stored as printed."


def emit() -> Path:
    pdf_seats = parse()
    for seat in pdf_seats:
        seat["source"] = PDF
        seat["full"] = f"{seat['given']} {seat['surname']}"
    page_seats = parse_pages()
    for seat in page_seats:
        seat["source"] = PAGES[seat["day"]]
        seat["full"] = seat["winner"]
    bulletin_seats = [seat for day_ in BULLETIN_DAYS for seat in read_bulletin(day_)]
    for seat in bulletin_seats:
        seat["source"] = bulletin_url(seat["day"])
    seats = sorted([*pdf_seats, *page_seats, *bulletin_seats], key=lambda seat: seat["day"])
    lines = pdf_lines()
    people = members(lines)
    fulls = {seat["member"]["name"]: seat["full"] for seat in pdf_seats}
    universe: dict[str, dict[str, Any]] = {}
    for member in people:
        universe[fold(member["name"])] = {"name": member["name"], "member": member, "died": iso_text(member["died"])}
    for each in candidate_names(lines):
        name_ = LIST_ALIASES.get(each, each)
        universe.setdefault(fold(name_), {"name": name_, "member": None, "died": None})
    holders = wikidata_rows(HOLDERS)
    politicians = wikidata_rows(POLITICIANS)

    def find(label: str, day_: date, text_: str) -> dict[str, Any] | None:
        if "(" in label:
            return None
        base = fold(LIST_ALIASES.get(label, label))
        hits = [
            entry
            for key, entry in universe.items()
            if (key == base or key.startswith(base + " ")) and (entry["died"] is None or entry["died"] >= day_)
        ]
        pick = PICKS.get((text_, label))
        if pick is not None:
            hits = [entry for entry in hits if entry["name"] == pick]
        return hits[0] if len(hits) == 1 else None

    def full_name(entry: dict[str, Any], label: str) -> str:
        if entry["name"] in fulls:
            return fulls[entry["name"]]
        if entry["name"] in SURNAMES:
            return SURNAMES[entry["name"]]
        name_, base = entry["name"], fold(LIST_ALIASES.get(label, label))
        cut = next((n for n in range(1, len(name_) + 1) if fold(name_[:n]) == base), None)
        if cut is None or cut == len(name_):
            cut = name_.index(" ")
        elif name_[cut] == "-":
            cut = name_.find(" ", cut)
        return f"{name_[cut:].strip()} {name_[:cut].strip()}"

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Federal Council elections by the United Federal Assembly, 1919–2025, with every ballot",
            polity="CH",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        switzerland = cs.polity("CH")
        sources: dict[str, UUID] = {}
        for title, url, urls in (
            (PDF_TITLE, PDF, [PDF]),
            (HTML_TITLE, ARCHIVE, list(PAGES.values())),
            (BULLETIN_TITLE, BULLETIN, [bulletin_url(day_) for day_ in BULLETIN_DAYS]),
        ):
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license=PUBLIC_RECORD,
            )
            for each in urls:
                cs.artifact(raw.cached(SOURCE, each), source)
                sources[each] = source
        office = cs.key("ch.office", "federal-councillor")
        cs.add("office", id=office, polity_id=switzerland, kind="other")
        name(office, "Member of the Federal Council of Switzerland")
        for text, lang in (
            ("Mitglied des Bundesrates", "de"),
            ("Membre du Conseil fédéral", "fr"),
            ("Membro del Consiglio federale", "it"),
        ):
            name(office, text, lang, "native")
        body = cs.key("ch.body", "united-federal-assembly")
        cs.add("body", id=body, polity_id=switzerland, kind="electoral_college")
        name(body, "United Federal Assembly")
        for text, lang in (
            ("Vereinigte Bundesversammlung", "de"),
            ("Assemblée fédérale réunie", "fr"),
            ("Assemblea federale plenaria", "it"),
        ):
            name(body, text, lang, "native")
        cs.add("scheme", code="ch.assembly", polity_id=switzerland, kind="electoral_district", rank=0, noun="assembly")
        unit = cs.unit("ch.assembly", "united-federal-assembly")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=switzerland,
            scheme_code="ch.assembly",
            code="united-federal-assembly",
            area_relation="unlocated",
            kind="aggregate",
        )
        name(unit, "United Federal Assembly in session")
        system = cs.key("ch.electoral_system", "federal-council")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=switzerland,
            rules={"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}]},
            rules_version=1,
        )
        name(system, "Election of the Federal Council by the United Federal Assembly")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)

        persons: dict[str, UUID] = {}
        items_of: dict[str, str] = {}

        def person(full: str) -> UUID:
            if full not in persons:
                persons[full] = cs.key(f"{SOURCE}.person", slug(full))
                if persons[full] in cs.minted:
                    cs.add("person", id=persons[full])
                    name(persons[full], full, "de")
            return persons[full]

        values: list[dict[str, Any]] = []
        elections: dict[date, tuple[UUID, str]] = {}
        seat_numbers: dict[date, int] = {}
        first_contest: dict[tuple[str, date], UUID] = {}
        for seat in seats:
            day_ = seat["day"]
            if day_ not in elections:
                election = cs.key("ch.election", f"{day_}:federal-council")
                cs.add(
                    "election",
                    id=election,
                    polity_id=switzerland,
                    kind="indirect",
                    date_from=day_,
                    date_to=day_,
                    status="held",
                )
                name(election, f"{day_.year} Federal Council election of {day_.day} {day_:%B}")
                path = f"ch/{day_}-federal-council"
                cs.add("slug", path=path, entity_id=election)
                cs.add("note", entity_id=election, kind="methodology", body_md=SOURCE_NOTE + " " + BLANK_NOTE)
                elections[day_] = election, path
            election, path = elections[day_]
            number_ = seat_numbers[day_] = seat_numbers.get(day_, 0) + 1
            winner_id = person(seat["full"])
            prefix = fold(seat["surname"] if "surname" in seat else seat["full"])
            rounds = seat["rounds"]
            previous = None
            for index, col in enumerate(rounds, 1):
                key = f"{day_}:federal-council:{number_}:{index}"
                contest = cs.key("ch.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    body_id=body,
                    magnitude=1,
                    round_number=index,
                    previous_round_id=previous,
                    electoral_system_id=system,
                    rules_override={"required": col["required"]} if "required" in col else {},
                    default_result_kind="counted",
                )
                label = f"Seat {number_}" if len(rounds) == 1 else f"Seat {number_}, ballot {index}"
                name(contest, label)
                cs.add(
                    "slug",
                    path=f"{path}/seat-{number_}" if len(rounds) == 1 else f"{path}/seat-{number_}/ballot-{index}",
                    entity_id=contest,
                )
                if index == 1 and seat["replaced"]:
                    cs.add(
                        "note",
                        entity_id=contest,
                        kind="context",
                        body_md=f"Election to the seat vacated by {seat['replaced'].title()}.",
                    )
                note = caveat(rounds, index - 1)
                if note:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=note)
                if index == 1:
                    first_contest[seat["full"], day_] = contest
                previous = contest
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[seat["source"]],
                    is_default=True,
                    completeness=1,
                )

                def value(measure: int, option: UUID | None, count: int) -> None:
                    values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": None if option is None else str(option),
                            "channel": 0,
                            "measure": measure,
                            "count_round": 0,
                            "value": count,
                            "flags": 0,
                        }
                    )

                won = None
                listed = list(col["names"])
                if "winner" in col:
                    listed.append((seat["surname"], col["winner"]))
                for label_, votes in listed:
                    is_winner = fold(label_) == prefix or fold(label_).startswith(prefix + " ")
                    option = cs.key("ch.option", f"{key}:{slug(label_)}")
                    entry = None if is_winner else find(label_, day_, f"{day_:%d.%m.%Y}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate" if is_winner or entry is not None else "write_in",
                        ballot_label=label_,
                    )
                    if is_winner or entry is not None:
                        cs.add(
                            "candidacy",
                            id=cs.key("ch.candidacy", f"{key}:{slug(label_)}"),
                            person_id=winner_id if entry is None else person(full_name(entry, label_)),
                            option_id=option,
                        )
                    if is_winner:
                        won = option
                    value(VOTES, option, votes)
                if col.get("more"):
                    option = cs.key("ch.option", f"{key}:others")
                    cs.add(
                        "contest_option", id=option, contest_id=contest, kind="others", ballot_label="Weitere Stimmen"
                    )
                    value(VOTES, option, col["more"])
                for measure, field in (
                    (PRESENT, "present"),
                    (CAST, "cast"),
                    (VALID, "valid"),
                    (INVALID, "invalid"),
                    (BLANK, "blank"),
                ):
                    if field in col:
                        value(measure, None, col[field])
                if index == len(rounds):
                    if won is None:
                        raise ValueError(f"{day_}: {seat['full']} not on the last ballot")
                    cs.add("outcome", result_set_id=result_set, option_id=won, kind="elected")
        wikidata = cs.source("Wikidata", WIKIDATA_TITLE)
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title=WIKIDATA_TITLE,
            url="https://www.wikidata.org/wiki/Q11811941",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in (HOLDERS, POLITICIANS):
            cs.artifact(raw.cached(SOURCE, url), wikidata)

        by_born: dict[date, list[dict[str, str]]] = {}
        by_label: dict[str, dict[str, dict[str, str]]] = {}
        for row in holders:
            born = iso(row.get("b"))
            if born is not None:
                by_born.setdefault(born, []).append(row)
        for row in [*holders, *politicians]:
            by_label.setdefault(fold(row["l"]), {})[row["p"].rsplit("/", 1)[1]] = row

        def item_of(full: str, born: str | None) -> str | None:
            if born is not None:
                items = {row["p"].rsplit("/", 1)[1] for row in by_born.get(day(born), [])}
                narrowed = {i for i in items if fold(full).split()[-1] in fold(by_label_of[i])}
                if len(narrowed) == 1:
                    return next(iter(narrowed))
            found = by_label.get(fold(full), {})
            if len(found) == 1:
                return next(iter(found))
            parts = fold(full).split()
            loose = {
                i
                for label, rows in by_label.items()
                for i, row in rows.items()
                if label.split()[0][:4] == parts[0][:4] and label.split()[-1:] == parts[-1:]
            }
            return next(iter(loose)) if len(loose) == 1 else None

        by_label_of = {row["p"].rsplit("/", 1)[1]: row["l"] for row in [*holders, *politicians]}
        born_of = {fulls[member["name"]]: member["born"] for member in people if member["name"] in fulls}
        known = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'wikidata' and value = any(%s)",
                [list(by_label_of)],
            ).fetchall()
        }
        linked: set[str] = set()
        for full, entity in persons.items():
            item = item_of(full, born_of.get(full))
            if item is None or item in linked or known.get(item, entity) != entity:
                continue
            linked.add(item)
            cs.add("external_id", entity_id=entity, scheme="wikidata", value=item)
            items_of[full] = item

        ends: dict[str, list[date | None]] = {}
        for row in holders:
            ends.setdefault(row["p"].rsplit("/", 1)[1], []).append(iso(row.get("end")))
        deaths = {row["p"].rsplit("/", 1)[1]: iso(row.get("d")) for row in holders}
        tenures: dict[str, tuple[date, date | None, date | None]] = {}
        for member in people:
            if member["name"] in fulls:
                tenures[fulls[member["name"]]] = (
                    day(member["elected"]),
                    iso_text(member["left"]),
                    iso_text(member["died"]),
                )
        for seat in seats:
            tenures.setdefault(seat["full"], (seat["day"], None, None))
        for full, (start, end, died) in tenures.items():
            item = items_of.get(full)
            if end is None and item is not None and None not in ends.get(item, [None]):
                end = max(d for d in ends[item] if d is not None)
            if died is None and item is not None:
                died = deaths.get(item)
            near = [c for (who, d), c in first_contest.items() if who == full and abs((d - start).days) <= 14]
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"{start}:{slug(full)}"),
                person_id=persons[full],
                office_id=office,
                from_date=start,
                to_date=end,
                acquired_via="elected",
                ended_via=None if end is None else ("died" if end == died else "resigned"),
                contest_id=near[0] if near else None,
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            ),
        )
        return cs.write()
