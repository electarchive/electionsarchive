"""Auckland Council elections: the mayor, the 13 governing body wards, and the 21 local boards.

The council's election site publishes each declaration of results as a PDF: one file for 2019, one combined file for
2022, and a file per kind of seat for 2025, plus the court-ordered second election of one local board subdivision in 2026.
A contest's result is the candidate totals the electoral officer declares for the whole ward or subdivision, so a
reporting unit here is one contest area, the finest level the authority publishes.
"""

import functools
import html
import re
import subprocess
import unicodedata
from collections import namedtuple
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "nz.akl"
SITE = "https://voteauckland.co.nz"
NEW_ZEALAND = str(uuid5(package.NAMESPACE, "polity\x1fNZ"))
REST = "https://services1.arcgis.com/n4yPwebTjJCmXB6W/ArcGIS/rest/services"
VOTES, INFORMAL, BLANK = 1, 5, 6

RESULTS = f"{SITE}/content/dam/elections/docs/results"
PAGE_2019 = f"{SITE}/en/past-local-elections-by-elections/2019-local-elections-results.html"
FILES = {
    2019: [f"{RESULTS}/2019/2019-local-elections-results-mayor-council.pdf"],
    2022: [f"{RESULTS}/2022/local-elections-2022-official-results-combined.pdf"],
    2025: [
        f"{RESULTS}/2025/mayor-local-elections-2025-official-results.pdf",
        f"{RESULTS}/2025/ward-councillor-local-elections-2025-official-results.pdf",
        f"{RESULTS}/2025/local-board-member-local-elections-2025-official-results.pdf",
    ],
}
DAYS = {2019: date(2019, 10, 12), 2022: date(2022, 10, 8), 2025: date(2025, 10, 11)}
RERUN_DAY = date(2026, 4, 9)
LAYERS = {
    "ward": ("Electoral_Ward", "WardName"),
    "local_board": ("Electoral_Local_Board", "LocalBoardName"),
    "subdivision": ("Electoral_Subdivision", "SubdivisionName"),
}

WARDS = [
    ("Albany", 2),
    ("Albert-Eden-Puketāpapa", 2),
    ("Franklin", 1),
    ("Howick", 2),
    ("Manukau", 2),
    ("Manurewa-Papakura", 2),
    ("Maungakiekie-Tāmaki", 1),
    ("North Shore", 2),
    ("Ōrākei", 1),
    ("Rodney", 1),
    ("Waitākere", 2),
    ("Waitematā and Gulf", 1),
    ("Whau", 1),
]
# Local boards in the order the declarations print them, each with its subdivisions where it has any.
BOARDS: list[tuple[str, list[str]]] = [
    ("Albert-Eden", ["Maungawhau", "Ōwairaka"]),
    ("Aotea / Great Barrier", []),
    ("Devonport-Takapuna", []),
    ("Franklin", ["Pukekohe", "Wairoa", "Waiuku"]),
    ("Henderson-Massey", []),
    ("Hibiscus and Bays", ["East Coast Bays", "Hibiscus Coast"]),
    ("Howick", ["Botany", "Flat Bush", "Howick", "Pakuranga"]),
    ("Kaipātiki", []),
    ("Māngere-Ōtāhuhu", []),
    ("Manurewa", []),
    ("Maungakiekie-Tāmaki", ["Maungakiekie", "Tāmaki"]),
    ("Ōrākei", []),
    ("Ōtara-Papatoetoe", ["Ōtara", "Papatoetoe"]),
    ("Papakura", []),
    ("Puketāpapa", []),
    ("Rodney", ["Dairy Flat", "Kumeū", "Northern Rodney", "Southern Kaipara", "Warkworth", "Wellsford"]),
    ("Upper Harbour", []),
    ("Waiheke", []),
    ("Waitākere Ranges", []),
    ("Waitematā", []),
    ("Whau", []),
]
# The contests of each declaration, in print order, and each one's seats where the PDF's text layer lacks them. The 2022
# file prints its headings as drawn shapes, so its order and seats were read from the page images and checked against the
# table of contents and the elected ranks.
NOT_IN_2022 = {"Flat Bush", "Northern Rodney", "Southern Kaipara"}
SEATS_2022_BOARDS = [4, 4, 5, 6, 4, 3, 2, 8, 4, 4, 3, 3, 3, 8, 7, 8, 3, 4, 7, 3, 4, 6, 6, 1, 4, 3, 1, 6, 5, 6, 7, 7]
NOT_IN_2025 = {"Wellsford"}
# The 2019 file lacks the contests that had one candidate; the results page names them.
UNOPPOSED_2019 = {
    ("ward", "Franklin", None): ["CASHMORE, Bill"],
    ("ward", "Rodney", None): ["SAYERS, Greg"],
    ("board", "Rodney", "Dairy Flat"): ["JOHNSTON, Louise"],
    ("board", "Rodney", "Wellsford"): ["SMITH, Colin"],
}
BOARDS_SUBS = dict(BOARDS)
Key = tuple[str, str | None, str | None]
Contest = namedtuple("Contest", "key seats rows unopposed pages")
Word = namedtuple("Word", "x0 y0 x1 y1 t")


def fold(text: str) -> str:
    text = text.replace("&", "and")
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)).lower())


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, name: str) -> str:
    return key("area", scheme, fold(name))


REGION = key("area", "region", "auckland")
AUTHORITY = key("source", SOURCE)
BOUNDARIES = key("source", "nz.akl.boundaries")
GOVERNING_BODY = key("body", "governing_body")
MAYORALTY = key("office", "mayor")
FPTP = key("electoral_system", "fptp")
BLOCK = key("electoral_system", "block")
UNIT_SCHEME = "nz.akl_contest"


def board_body(board: str) -> str:
    return key("body", "local_board", fold(board))


def fetch() -> None:
    for urls in FILES.values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, PAGE_2019, suffix=".html")
    for name, _ in LAYERS.values():
        raw.fetch(SOURCE, layer_url(name), suffix=".geojson")


def layer_url(name: str) -> str:
    return f"{REST}/{name}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{year}" for year in FILES),
        f"{SOURCE}/2026-papatoetoe",
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for year in FILES:
        election(year).write()
    rerun().write()


def emit() -> None:
    package.run("load", packages())


# ---- the PDFs -------------------------------------------------------------------------------------------------------


def _pages(path: str) -> list[list[Word]]:
    text = subprocess.run(["pdftotext", "-bbox", path, "-"], capture_output=True, text=True, check=True).stdout
    word = r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>'
    return [
        [Word(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in re.findall(word, page)]
        for page in text.split("<page ")[1:]
    ]


def _mid(w: Word) -> float:
    return (w.y0 + w.y1) / 2


def _lines(words: list[Word]) -> list[tuple[float, str]]:
    grouped: list[tuple[float, list[Word]]] = []
    for w in sorted(words, key=lambda w: (w.y0, w.x0)):
        if grouped and abs(grouped[-1][0] - w.y0) < 3:
            grouped[-1][1].append(w)
        else:
            grouped.append((w.y0, [w]))
    return [(y, " ".join(x.t for x in sorted(line, key=lambda x: x.x0))) for y, line in grouped]


def _rows(body: list[Word], cols: tuple[float, float, float, float]) -> list[dict[str, object]]:
    """The candidate rows of a ruled table: name, affiliation (which may wrap onto two lines), votes, and rank."""
    ax, vx0, vx1, rx = cols
    rank_words = [w for w in body if re.fullmatch(r"\d+=?|\*+", w.t) and w.x0 >= rx - 6 and w.x1 > vx1 + 3]
    vote_words = [
        w for w in body if re.fullmatch(r"\d[\d,]*", w.t) and w.x1 <= rx + 2 and w.x0 > ax + 40 and w not in rank_words
    ]
    rows: list[dict[str, object]] = []
    for v in sorted(vote_words, key=lambda w: w.y0):
        name = " ".join(w.t for w in sorted(body, key=lambda w: w.x0) if w.x1 <= ax + 1 and abs(_mid(w) - _mid(v)) < 3)
        rank = [w.t for w in rank_words if abs(_mid(w) - _mid(v)) < 3]
        rows.append({"name": name, "y": _mid(v), "votes": int(v.t.replace(",", "")), "rank": rank[0] if rank else None})
    pieces: dict[int, list[Word]] = {i: [] for i in range(len(rows))}
    if rows:
        for w in body:
            if w.x0 >= ax - 1 and w.x1 <= vx0 + 2 and w not in vote_words:
                near = min(range(len(rows)), key=lambda i: abs(float(rows[i]["y"]) - _mid(w)))  # ty: ignore
                if abs(float(rows[near]["y"]) - _mid(w)) < 12:  # ty: ignore
                    pieces[near].append(w)
    for i, row in enumerate(rows):
        row["affiliation"] = " ".join(w.t for w in sorted(pieces[i], key=lambda w: (round(w.y0), w.x0)))
    out: list[dict[str, object]] = []
    for row in rows:
        out.append(row)
        if row["name"] == "Blank":
            break
    return out


def _tables(path: str) -> list[dict[str, object]]:
    """Every results table of a declaration, in print order, with the lines printed above it."""
    found: list[dict[str, object]] = []
    cols: tuple[float, float, float, float] | None = None
    for number, words in enumerate(_pages(path), 1):
        footer = min([w.y0 for w in words if w.t.startswith("voteauckland") or w.t == "Back"] or [1e9])
        words = [w for w in words if w.y0 < footer - 1]
        heads = sorted(
            (
                w
                for w in words
                if w.t == "Candidate" and any(v.t == "Affiliation" and abs(v.y0 - w.y0) < 2 for v in words)
            ),
            key=lambda w: w.y0,
        )
        if found and not found[-1]["unopposed"] and cols and found[-1]["rows"][-1]["name"] != "Blank":  # ty: ignore
            limit = heads[0].y0 if heads else 1e9
            found[-1]["rows"] += _rows([w for w in words if w.y0 < limit - 1], cols)  # ty: ignore
            found[-1]["pages"].append(number)  # ty: ignore
        previous = 0.0
        for index, head in enumerate(heads):
            bottom = heads[index + 1].y0 if index + 1 < len(heads) else 1e9
            same = lambda t, head=head: [v for v in words if v.t == t and abs(v.y0 - head.y0) < 2]
            aff, votes, rank = same("Affiliation")[0], same("Votes"), same("Rank")
            body = [w for w in words if w.y0 > head.y1 - 0.5 and w.y0 < bottom - 1]
            above = [w for w in words if previous <= w.y0 < head.y0 - 0.5]
            rows: list[dict[str, object]] = []
            unopposed = False
            if votes and rank:
                cols = (aff.x0, votes[0].x0, votes[0].x1, rank[0].x0)
                rows = _rows(body, cols)
            if rows:
                previous = max(float(r["y"]) for r in rows) + 6  # ty: ignore
            else:
                unopposed = True
                stop = [w for w in body if w.t == "**" and w.x0 < aff.x0]
                limit = min((w.y0 for w in stop), default=bottom)
                names = [w for w in body if w.y0 < limit - 1 and w.x1 <= aff.x0 + 1]
                rows = [{"name": t, "y": y + 5, "votes": None, "rank": None} for y, t in _lines(names)]
                for w in body:
                    if w.y0 < limit - 1 and aff.x0 - 1 <= w.x0 and w.x1 < aff.x0 + 190:
                        near = min(rows, key=lambda r: abs(float(r["y"]) - _mid(w)))  # ty: ignore
                        if abs(float(near["y"]) - _mid(w)) < 12:  # ty: ignore
                            near["affiliation"] = (str(near.get("affiliation", "")) + " " + w.t).strip()
                for row in rows:
                    row.setdefault("affiliation", "")
                previous = limit + 14
            found.append(
                {
                    "pages": [number],
                    "rows": rows,
                    "unopposed": unopposed,
                    "above": [t for _, t in _lines(above)],
                }
            )
    return found


def _resolve(heading: str, subdivisions: dict[str, list[str]] | None = None) -> Key:
    """The contest a printed heading names, whatever spacing the text layer gave it."""
    text = fold(heading)
    if "mayor" in text:
        return ("mayor", None, None)
    if "localboard" in text:
        for board, subs in sorted(BOARDS, key=lambda b: -len(fold(b[0]))):
            if fold(board) + "localboard" in text or text.startswith(fold(board) + "subdivisionofthe"):
                for sub in subs:
                    if fold(sub) + "subdivision" in text.replace(fold(board) + "localboard", ""):
                        return ("board", board, sub)
                return ("board", board, None)
    if "subdivisionofthe" in text:
        for board, subs in BOARDS:
            if text.endswith(fold(board) + "localboard"):
                for sub in subs:
                    if text.startswith(fold(sub) + "subdivision"):
                        return ("board", board, sub)
    if text.endswith("ward"):
        for ward, _ in WARDS:
            if text == fold(ward) + "ward":
                return ("ward", ward, None)
    raise ValueError(f"unrecognized contest heading {heading!r}")


def _seats(lines: list[str]) -> int | None:
    for line in reversed(lines):
        if m := re.fullmatch(r"Positions: (\d+)", line.strip()):
            return int(m.group(1))
    return None


def _contest(table: dict[str, object], k: Key, seats: int | None) -> Contest:
    rows = table["rows"]
    assert isinstance(rows, list)
    unopposed = bool(table["unopposed"])
    elected = [r for r in rows if unopposed or (r["rank"] and str(r["rank"])[0].isdigit())]
    pages = table["pages"]
    assert isinstance(pages, list)
    if seats is None:
        seats = len(elected)
    if len(elected) != seats:
        raise ValueError(f"{k}: {len(elected)} ranked candidates for {seats} positions")
    if not unopposed and not (rows[-1]["name"] == "Blank" and rows[-2]["name"] == "Informal"):
        raise ValueError(f"{k}: the table does not end with its informal and blank votes")
    return Contest(k, seats, rows, unopposed, pages)


@functools.cache
def _contests(year: int) -> list[Contest]:
    out: list[Contest] = []
    if year == 2019:
        out = _contests_2019()
    elif year == 2022:
        tables = _tables(str(raw.cached(SOURCE, FILES[2022][0]).path))[:46]
        keys: list[Key] = [("mayor", None, None), *(("ward", w, None) for w, _ in WARDS)]
        for board, _ in BOARDS:
            subs = [x for x in BOARDS_SUBS[board] if x not in NOT_IN_2022] or [None]
            keys += [("board", board, s) for s in subs]
        seats = [1, *(n for _, n in WARDS), *SEATS_2022_BOARDS]
        if len(tables) != len(keys) != len(seats):
            raise ValueError(f"2022: {len(tables)} tables for {len(keys)} contests")
        out = [_contest(t, k, n) for t, k, n in zip(tables, keys, seats, strict=True)]
    else:
        mayor, wards, boards = (_tables(str(raw.cached(SOURCE, url).path)) for url in FILES[2025])
        keys = [("ward", w, None) for w, _ in WARDS]
        out = [_contest(mayor[0], ("mayor", None, None), 1)]
        out += [_contest(t, k, _seats(t["above"])) for t, k in zip(wards, keys, strict=True)]  # ty: ignore
        board_keys: list[Key] = []
        for board, _ in BOARDS:
            subs = [x for x in BOARDS_SUBS[board] if x not in NOT_IN_2025] or [None]
            board_keys += [("board", board, s) for s in subs]
        if len(boards) != len(board_keys):
            raise ValueError(f"2025: {len(boards)} local board tables for {len(board_keys)} contests")
        for t, k in zip(boards, board_keys, strict=True):
            named = []
            for line in t["above"]:  # ty: ignore
                if not re.search(r"(Local Board|Subdivision\s*\))$", line.strip()):
                    continue
                try:
                    named.append(_resolve(line))
                except ValueError:
                    continue
            if named and named[-1] != k:
                raise ValueError(f"2025: the heading {named[-1]} is not {k}")
            out.append(_contest(t, k, _seats(t["above"])))  # ty: ignore
    for contest in out:
        if contest.key[0] == "ward":
            seats = dict(WARDS)[str(contest.key[1])]
            if contest.seats != seats:
                raise ValueError(f"{year} {contest.key}: {contest.seats} seats, not {seats}")
    return out


def _contests_2019() -> list[Contest]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, FILES[2019][0]).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    out: list[Contest] = []
    for block in text.split("Issue:")[1:]:
        head, _, rest = block.partition("\n")
        k = _resolve(head.strip())
        seats = int(re.search(r"Number of vacancies:\s+(\d+)", rest).group(1))  # ty: ignore
        rows: list[dict[str, object]] = []
        for line in rest.splitlines():
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) < 3 or not parts[0].isdigit() or not re.fullmatch(r"\d+(?: \d+=?)?", parts[-1]):
                continue
            number = parts[-1].split()
            rows.append(
                {
                    "name": parts[1],
                    "votes": int(number[0]),
                    "rank": number[1] if len(number) > 1 else None,
                    "affiliation": parts[2] if len(parts) > 3 else "",
                }
            )
        out.append(_contest({"rows": rows, "unopposed": False, "pages": []}, k, seats))
    unopposed = {k: v for k, v in UNOPPOSED_2019.items()}
    page = _elected_2019()
    for k, names in unopposed.items():
        if page.get(k) != {n for n in names}:
            raise ValueError(f"2019 results page disagrees on the unopposed {k}")
        rows = [{"name": n, "votes": None, "rank": None, "affiliation": ""} for n in names]
        out.append(_contest({"rows": rows, "unopposed": True, "pages": []}, k, len(names)))
    # A candidate elected to a ward who also topped a board poll is declared out of the board, and the next candidate in.
    # The PDF ranks the poll alone and prints no footnote in 2019, so the results page's list of members settles it.
    councillors = {str(r["name"]) for c in out if c.key[0] == "ward" for r in c.rows if _elected(c, r)}
    for index, contest in enumerate(out):
        want = page.get(contest.key)
        got = {str(r["name"]) for r in contest.rows if _elected(contest, r)}
        if want is None or {fold(n) for n in want} == {fold(n) for n in got}:
            continue
        gone = {n for n in got if fold(n) not in {fold(w) for w in want}}
        up = {w for w in want if fold(w) not in {fold(n) for n in got}}
        if len(gone) != 1 or len(up) != 1 or not gone <= councillors or contest.key[0] != "board":
            raise ValueError(f"2019 {contest.key}: the results page names {sorted(want)}, the PDF {sorted(got)}")
        rows = [dict(r) for r in contest.rows]
        for row in rows:
            if row["name"] in gone:
                row["rank"] = "*"
            elif fold(str(row["name"])) == fold(next(iter(up))):
                row["rank"] = str(contest.seats + 1)
        out[index] = contest._replace(rows=rows)
    return out


def _elected(contest: Contest, row: dict[str, object]) -> bool:
    return contest.unopposed or bool(row["rank"] and str(row["rank"])[0].isdigit())


@functools.cache
def _elected_2019() -> dict[Key, set[str]]:
    """The members the results page lists for each area, to check the PDF against."""
    text = html.unescape(html.unescape(raw.cached(SOURCE, PAGE_2019).path.read_text()))
    text = text.replace("\\r\\n", "").replace("\u200b", "")
    out: dict[Key, set[str]] = {}
    for label, cell in re.findall(r"<td>([^<]*)</td>\s*<td>(.*?)</td>", text, re.DOTALL):
        try:
            k = _resolve(label.replace("subdivision)", "Subdivision)").replace("local board", "Local Board").strip())
        except ValueError:
            continue
        names = re.findall(r"([A-ZĀĒĪŌŪ][^<\n]*?)(?:\s*\(unopposed\))?\s*(?:<|$)", cell.replace("<p>", "<").strip())
        out.setdefault(k, set()).update(n.strip() for n in names if "," in n)
    return out


# ---- names ----------------------------------------------------------------------------------------------------------


def person_name(printed: str) -> str:
    surname, _, given = printed.partition(",")

    def cap(word: str) -> str:
        word = word.title()
        word = re.sub(r"^(Mc|Mac(?=[a-z]{3}))([a-z])", lambda m: m.group(1) + m.group(2).upper(), word)
        return re.sub(r"(?<=[-'])([a-z])", lambda m: m.group(1).upper(), word)

    surname = " ".join(cap(w) for w in surname.split())
    return f"{given.strip()} {surname}".strip()


def party_key(affiliation: str) -> str | None:
    folded = fold(affiliation)
    return None if folded in ("", "independent") else key("party", folded)


def _label(k: Key) -> str:
    kind, name, sub = k
    if kind == "mayor":
        return "Mayor of Auckland"
    if kind == "ward":
        return f"{name} Ward"
    return f"{name} Local Board" + (f", {sub} Subdivision" if sub else "")


def _area_for(k: Key) -> str:
    kind, name, sub = k
    if kind == "mayor":
        return REGION
    if kind == "ward":
        return area("ward", str(name))
    return area("subdivision", f"{name}:{sub}") if sub else area("local_board", str(name))


# ---- geography ------------------------------------------------------------------------------------------------------


def _layer(name: str) -> list[dict[str, object]]:
    import json

    return json.loads(raw.cached(SOURCE, layer_url(name)).path.read_text())["features"]


def _props(feature: dict[str, object]) -> dict[str, object]:
    properties = feature["properties"]
    assert isinstance(properties, dict)
    return properties


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Auckland's 13 governing body wards, 21 local boards, and local board subdivisions from Auckland Council's "
        "electoral boundary layers",
    )
    p.external.add(NEW_ZEALAND)
    p.add(
        "source",
        id=BOUNDARIES,
        kind="official_body",
        publisher="Auckland Council",
        title="Electoral Ward, Electoral Local Board, and Electoral Subdivision boundaries (Auckland Council open data)",
        url="https://data-aucklandcouncil.opendata.arcgis.com/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Auckland Council",
        notes="The layers carry the version '2023 Provisional ver', in operation from 12 October 2022. Subdivisions "
        "created for 2025 (Flat Bush, Northern Rodney, and Southern Kaipara) are not in them.",
    )
    schemes = [
        ("nz.akl_region", 1, "administrative", "region"),
        ("nz.akl_ward", 2, "electoral_district", "ward"),
        ("nz.akl_local_board", 2, "electoral_district", "local board"),
        ("nz.akl_subdivision", 3, "electoral_district", "local board subdivision"),
        (UNIT_SCHEME, 4, "synthetic", "contest total"),
    ]
    layers: dict[str, dict[str, dict[str, object]]] = {}
    for scheme, rank, kind, noun in schemes:
        p.add("scheme", code=scheme, polity_id=NEW_ZEALAND, kind=kind, rank=rank, noun=noun)
    for kind, (name, field) in LAYERS.items():
        p.artifact(raw.cached(SOURCE, layer_url(name)), BOUNDARIES)
        layers[kind] = {fold(str(_props(f)[field])): f for f in _layer(name)}
        retrieved = raw.cached(SOURCE, layer_url(name)).retrieved_at
        p.add(
            "boundary_set",
            id=key("boundary_set", kind, 2022),
            scheme_code=f"nz.akl_{kind}",
            vintage="2022",
            valid_from=date(2022, 10, 12),
            as_of=retrieved.date(),
        )
    p.add("area", id=REGION, scheme_code="nz.akl_region")
    p.name(REGION, "Auckland", "en", "official")
    p.add(
        "boundary_set",
        id=key("boundary_set", "region", 2022),
        scheme_code="nz.akl_region",
        vintage="2022",
        valid_from=date(2022, 10, 12),
        as_of=raw.cached(SOURCE, layer_url("Electoral_Ward")).retrieved_at.date(),
    )

    def outline(id: str, shape: shapely.Geometry) -> None:
        shape = shapely.set_srid(
            shapely.MultiPolygon(
                [
                    g
                    for g in shapely.get_parts(shapely.make_valid(shape.simplify(0.0001, preserve_topology=True)))
                    if shapely.get_type_id(g) in (3, 6)
                    for g in shapely.get_parts(g)
                ]
            ),
            4326,
        )
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=BOUNDARIES,
        )

    shapes = {
        kind: {n: shapely.geometry.shape(f["geometry"]) for n, f in features.items()}
        for kind, features in layers.items()
    }
    outline(REGION, shapely.union_all(list(shapes["ward"].values())))
    for ward, _ in WARDS:
        id = area("ward", ward)
        p.add("area", id=id, scheme_code="nz.akl_ward")
        p.add("area_code", area_id=id, code=str(_props(layers["ward"][fold(ward)])["WardCode"]))
        p.add("area_parent", area_id=id, parent_id=REGION)
        p.name(id, f"{ward} Ward", "en", "official")
        outline(id, shapes["ward"][fold(ward)])
    for board, subs in BOARDS:
        id = area("local_board", board)
        p.add("area", id=id, scheme_code="nz.akl_local_board")
        p.add("area_code", area_id=id, code=str(_props(layers["local_board"][fold(board)])["LocalBoardCode"]))
        p.add("area_parent", area_id=id, parent_id=REGION)
        p.name(id, f"{board} Local Board", "en", "official")
        outline(id, shapes["local_board"][fold(board)])
        for sub in subs:
            sid = area("subdivision", f"{board}:{sub}")
            p.add("area", id=sid, scheme_code="nz.akl_subdivision")
            p.add("area_parent", area_id=sid, parent_id=id)
            p.name(sid, f"{sub} Subdivision, {board} Local Board", "en", "official")
            feature = layers["subdivision"].get(fold(sub))
            if feature is not None:
                p.add("area_code", area_id=sid, code=str(_props(feature)["SubdivisionCode"]))
                outline(sid, shapes["subdivision"][fold(sub)])
    return p


# ---- registry -------------------------------------------------------------------------------------------------------

SYSTEMS = [
    (
        FPTP,
        {"formula": "fptp"},
        "One position: the candidate with the most votes is elected (first past the post).",
    ),
    (
        BLOCK,
        {"formula": "block"},
        (
            "Several positions: each elector votes for up to as many candidates as there are positions, and the "
            "candidates with the most votes fill them (first past the post in a multi-member area). A tie for the "
            "last position is settled by lot."
        ),
    ),
]


def _affiliations() -> dict[str, str]:
    seen: dict[str, str] = {}
    for year in FILES:
        for contest in _contests(year):
            for row in contest.rows:
                text = str(row.get("affiliation") or "")
                if party_key(text) and fold(text) not in seen:
                    seen[fold(text)] = text
    for row in _rerun_contest().rows:
        text = str(row.get("affiliation") or "")
        if party_key(text) and fold(text) not in seen:
            seen[fold(text)] = text
    return seen


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Auckland Council's governing body, mayoralty, local boards, electoral systems, and the affiliations "
        "candidates printed",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(NEW_ZEALAND)
    p.add(
        "source",
        id=AUTHORITY,
        kind="official_body",
        publisher="Auckland Council",
        title="Auckland local election results (declarations of results)",
        url=f"{SITE}/en/past-local-elections-by-elections.html",
        attribution="Auckland Council",
        notes="The council's election site states no license for the results pages.",
    )
    p.add("body", id=GOVERNING_BODY, polity_id=NEW_ZEALAND, kind="council")
    p.name(GOVERNING_BODY, "Auckland Council Governing Body", "en", "official")
    p.add("office", id=MAYORALTY, polity_id=NEW_ZEALAND, body_id=GOVERNING_BODY, kind="mayor")
    p.name(MAYORALTY, "Mayor of Auckland", "en", "official")
    for board, _ in BOARDS:
        id = board_body(board)
        p.add("body", id=id, polity_id=NEW_ZEALAND, kind="council")
        p.name(id, f"{board} Local Board", "en", "official")
    for id, rules, method in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=NEW_ZEALAND, valid_from=date(2010, 1, 1), rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for folded, text in sorted(_affiliations().items()):
        id = party_key(text)
        assert id is not None
        p.add("party", id=id, kind="party", polity_id=NEW_ZEALAND)
        p.name(id, text, "en", "official")
    return p


# ---- elections ------------------------------------------------------------------------------------------------------


def _add_contest(p: Package, election: str, path: str, day: date, year: str, c: Contest, source: str) -> None:
    label = _label(c.key)
    kind, name, _sub = c.key
    contest = key("contest", day, *(str(x) for x in c.key))
    unopposed = c.unopposed
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office" if kind == "mayor" else "body_seats",
        office_id=MAYORALTY if kind == "mayor" else None,
        body_id=None if kind == "mayor" else GOVERNING_BODY if kind == "ward" else board_body(str(name)),
        district_area_id=_area_for(c.key),
        magnitude=c.seats,
        round_number=1,
        electoral_system_id=FPTP if c.seats == 1 else BLOCK,
        default_result_kind="tacit" if unopposed else "counted",
    )
    p.name(contest, f"{year} {label}", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug(label)}", entity_id=contest, is_current=True)
    declined = [str(r["name"]) for r in c.rows if str(r.get("rank") or "").startswith("*")]
    result_set = key("result_set", day, *(str(x) for x in c.key))
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if unopposed else "counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=None if unopposed else 1.0,
    )
    if declined:
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=f"{', '.join(declined)} polled among the leading candidates but was declared elected to the "
            "governing body, so the next highest polling candidate was declared elected to this seat.",
            lang="en",
        )
    unit = key("unit", day, *(str(x) for x in c.key))
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=NEW_ZEALAND,
        scheme_code=UNIT_SCHEME,
        code=f"{day}:{fold(label)}",
        area_relation="equals",
        area_id=_area_for(c.key),
        kind="aggregate",
    )
    p.name(unit, label, "en", "official")
    for position, row in enumerate(c.rows, 1):
        printed = str(row["name"])
        if printed in ("Informal", "Blank"):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=INFORMAL if printed == "Informal" else BLANK,
                count_round=0,
                flags=0,
                value=row["votes"],
            )
            continue
        slot = f"{day}:{'/'.join(str(x) for x in c.key)}:{printed}"
        person = key("person", slot)
        p.add("person", id=person)
        p.name(person, person_name(printed), "en", "official")
        option = key("option", slot)
        affiliation = str(row.get("affiliation") or "")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party_key(affiliation),
            ballot_position=position,
            ballot_label=printed,
        )
        p.add(
            "candidacy",
            id=key("candidacy", slot),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=printed,
            is_independent=fold(affiliation) == "independent",
        )
        rank = row.get("rank")
        if unopposed or (rank and str(rank)[0].isdigit()):
            outcome = "elected" if kind != "mayor" else "won"
        elif rank and str(rank).startswith("*"):
            outcome = "declined"
        else:
            outcome = "lost"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
        if row["votes"] is not None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=row["votes"],
            )


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Auckland Council elections: the mayor, the governing body wards, and the local boards, with each "
        "contest's candidate totals from the council's declaration of results",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(NEW_ZEALAND)
    for url in FILES[year]:
        p.uses(raw.cached(SOURCE, url))
    if year == 2019:
        p.uses(raw.cached(SOURCE, PAGE_2019))
    id = key("election", day)
    path = f"nz/{day}-auckland-council"
    p.add("election", id=id, polity_id=NEW_ZEALAND, kind="local", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{year} Auckland Council elections", "en", "official")
    for c in _contests(year):
        if year == 2025 and c.key == ("board", "Ōtara-Papatoetoe", "Papatoetoe"):
            continue
        _add_contest(p, id, path, day, str(year), c, AUTHORITY)
    return p


@functools.cache
def _rerun_contest() -> Contest:
    return next(c for c in _contests(2025) if c.key == ("board", "Ōtara-Papatoetoe", "Papatoetoe"))


def rerun() -> Package:
    p = Package(
        f"{SOURCE}/2026-papatoetoe",
        SOURCE,
        "The 2026 election for the Papatoetoe subdivision of the Ōtara-Papatoetoe Local Board, held again after a court "
        "voided the 2025 election there",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2025"],
    )
    p.external.add(NEW_ZEALAND)
    p.uses(raw.cached(SOURCE, FILES[2025][2]))
    id = key("election", RERUN_DAY)
    path = f"nz/{RERUN_DAY}-papatoetoe-subdivision"
    p.add(
        "election",
        id=id,
        polity_id=NEW_ZEALAND,
        kind="local",
        date_from=RERUN_DAY,
        date_to=RERUN_DAY,
        status="held",
        rerun_of_id=key("election", DAYS[2025]),
    )
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, "2026 Papatoetoe Subdivision election", "en", "official")
    p.add(
        "note",
        id=key("note", id),
        entity_id=id,
        kind="methodology",
        body_md="On 16 December 2025 the Manukau District Court voided the October 2025 election of the Papatoetoe "
        "subdivision of Ōtara-Papatoetoe Local Board and ordered a new one. Postal voting ran from 9 March to "
        "noon on 9 April 2026. The council's declaration for the October 2025 vote is not published.",
        lang="en",
    )
    _add_contest(p, id, path, RERUN_DAY, "2026", _rerun_contest(), AUTHORITY)
    return p
