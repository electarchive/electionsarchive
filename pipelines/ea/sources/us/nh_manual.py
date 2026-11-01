import difflib
import functools
import hashlib
import itertools
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import polars as pl

from ea import raw
from ea.changeset import slug
from ea.store import DATA

SOURCE = "us.nh_manual"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ITEM = "https://archive.org/download/{item}/{item}{kind}.pdf"
PUBLISHER = "New Hampshire Secretary of State"
COUNTIES = (
    "BELKNAP",
    "CARROLL",
    "CHESHIRE",
    "COOS",
    "GRAFTON",
    "HILLSBOROUGH",
    "MERRIMACK",
    "ROCKINGHAM",
    "STRAFFORD",
    "SULLIVAN",
)
ITEMS = {
    **{year: f"manualforgeneral{(year - 1886) // 2}newh" for year in range(1970, 2007, 2)},
    2010: "manualforgeneral2011newh",
    2012: "manualforgeneral2013newh",
    2014: "manualforgeneral2015newh",
}
UNH_2008 = "https://web.archive.org/web/2024id_/https://scholars.unh.edu/cgi/viewcontent.cgi?article=1060&context=court"
OFFICES = (
    (r"sheriff", "sheriff"),
    (r"(?:county\s+)?attorney", "district attorney"),
    (r"(?:county\s+)?treasurer", "treasurer"),
    (r"register\s+of\s+deeds", "register of deeds"),
    (r"register\s+of\s+probate", "register of probate"),
    (r"(?:county\s+)?commissioners?", "board of commissioners"),
)
TITLES = {
    "sheriff": "Sheriff",
    "district attorney": "District Attorney",
    "treasurer": "Treasurer",
    "register of deeds": "Register of Deeds",
    "register of probate": "Register of Probate",
    "board of commissioners": "Board of Commissioners",
}
NAMES = {
    "sheriff": ["sheriff"],
    "district attorney": ["attorney", "county attorney"],
    "treasurer": ["treasurer", "county treasurer"],
    "register of deeds": ["register of deeds"],
    "register of probate": ["register of probate"],
    "board of commissioners": ["commissioners", "county commissioners"],
}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
NOISE = re.compile(r"[\^_'•■*]")
LEADER = re.compile(r"^[.\-—•\s]+$")
PARTY = re.compile(r"(?:[,.]\s*(r&d|d&r|r|d|i|ind|l|lib|1)|\s(?-i:(r|d|l)))\s*\.?\s*(?:\(write-?in\))?$", re.IGNORECASE)
CLEAN = re.compile(
    r"[A-Z][A-Za-z'.\-]*(?: (?:[A-Z]|de[A-Z]|(?:de|du|dupont|van|von|der|la|di) (?=[A-Z]|[a-z]+ [A-Z]))[A-Za-z'.\-]*)+(?:,? (?:Jr|Sr|II|III|IV)\.?)?"
)
TAIL = re.compile(r"[A-Z][A-Za-z'\-]+(?:,? [A-Za-z'\-.]+){0,3}")
PERIOD_TOWN = re.compile(
    r"(.+?(?<! St)(?<! Mt)[a-z])\.,? (?:(Jr|Sr|II|III|IV)\.?,? )?([A-Z][a-z]{2,}(?: [A-Z][a-z]+)?)"
)
STRANDED = re.compile(r"(.+[a-z])\. ([A-Z][a-z]+)\. (d|r)")
TRAILING = re.compile(r"(.+?[A-Za-z.]) (d|r)")


class Word(NamedTuple):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


class Row(NamedTuple):
    page: int
    width: float
    height: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


def years() -> list[int]:
    return sorted([*ITEMS, 2008])


def candidate_pages(pdf: Path) -> list[int]:
    layout = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    pages = layout.split("\f")
    hits = [
        number
        for number, text in enumerate(pages, 1)
        if re.search(
            r"For\s+(the\s+)?(Sheriff|County\s+Attorney|Attorney|Register\s+of\s+Deeds|Treasurer)", text, re.IGNORECASE
        )
        and re.search(rf"[A-Z]{{4,}} COUNTY|COUNTY OFFICES|(?im:^\s*(?:{'|'.join(COUNTIES)})\s+COUNTY\s*:?\s*$)", text)
    ]
    keep = set(hits)
    for left, right in itertools.pairwise(hits):
        if right - left <= 3:
            keep.update(range(left, right))
    keep.update(number + 1 for number in hits)
    return sorted(keep)


def words(pdf: Path, first: int, last: int) -> dict[int, tuple[float, float, list[Word]]]:
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(first), "-l", str(last), str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    pages: dict[int, tuple[float, float, list[Word]]] = {}
    number = first - 1
    for page in re.split(r"(?=<page )", out)[1:]:
        number += 1
        size = re.match(r'<page width="([\d.]+)" height="([\d.]+)"', page)
        assert size is not None
        found = [
            Word(
                text.replace("&amp;", "&")
                .replace("&quot;", '"')
                .replace("&apos;", "'")
                .replace("&lt;", "<")
                .replace("&gt;", ">"),
                float(a),
                float(b),
                float(c),
                float(d),
            )
            for a, b, c, d, text in re.findall(
                r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', page
            )
        ]
        pages[number] = (float(size[1]), float(size[2]), found)
    return pages


def rows(pages: dict[int, tuple[float, float, list[Word]]]) -> list[Row]:
    out: list[Row] = []
    for number, (width, height, found) in pages.items():
        ordered = sorted(found, key=lambda word: (word.y0 + word.y1) / 2)
        current: list[Word] = []
        for word in ordered:
            centre = (word.y0 + word.y1) / 2
            if current and abs(centre - sum((w.y0 + w.y1) / 2 for w in current) / len(current)) > 0.55 * (
                word.y1 - word.y0
            ):
                out.append(Row(number, width, height, sorted(current, key=lambda w: w.x0)))
                current = []
            current.append(word)
        if current:
            out.append(Row(number, width, height, sorted(current, key=lambda w: w.x0)))
    return out


def number(tokens: list[Word]) -> tuple[int, bool, list[Word]] | None:
    taken: list[Word] = []
    for word in reversed(tokens):
        if (re.fullmatch(r"[\d,.\^_'•■*]+", word.text) and re.search(r"\d", word.text)) or (word.text == "," and taken):
            taken.insert(0, word)
        elif LEADER.fullmatch(word.text) and taken:
            continue
        else:
            break
    if not taken:
        return None
    joined = NOISE.sub("", "".join(word.text for word in taken)).strip(".,")
    clean = len(taken) == 1 and re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", taken[0].text) is not None
    joined = re.sub(r"(?<=\d)\.(?=\d{3}$)", ",", joined)
    if not re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d+", joined):
        return None
    return int(joined.replace(",", "")), clean, taken


def header(text: str) -> tuple[str, str] | None:
    plain = re.sub(r"\s+", " ", text).strip()
    plain = re.sub(r"(?i)^(For\s+(?:the\s+)?)Counts'?\b", r"\1County", plain)
    plain = re.sub(
        r"(?i)^[^\sA-Za-z]?[A-Za-z]?[-~.'’]?[A-Za-z]{1,3}\s+(?=(?:register|sheriff|(?:county\s+)?(?:attorney|treasurer|commissioners?))\b)",
        "For ",
        plain,
    )
    county = re.fullmatch(r"([A-Za-z]{4,12}) COUNT[A-Z']{1,2}:?", plain, re.IGNORECASE)
    if county:
        match = difflib.get_close_matches(county[1].upper(), COUNTIES, n=1, cutoff=0.7)
        if match:
            return "county", match[0]
    bare = re.fullmatch(r"(.*?):", plain)
    if bare and not re.match(r"(?i)for\b", plain):
        for pattern, name in OFFICES:
            if re.fullmatch(pattern, bare[1].strip(), re.IGNORECASE):
                return "office", name
    found = re.match(r"For\s+(?:the\s+)?(.*?)[:;.]?$", plain, re.IGNORECASE)
    if found:
        for pattern, name in OFFICES:
            if re.fullmatch(pattern, found[1].strip(), re.IGNORECASE):
                return "office", name
        phrase = re.sub(r"\s+", " ", found[1].strip().lower())
        if len(phrase) > 4:
            for pattern, name in OFFICES:
                if difflib.get_close_matches(phrase, NAMES[name], n=1, cutoff=0.72):
                    return "office", name
        return "other", plain
    word = re.fullmatch(r"([A-Za-z0-9\- ]{3,8})\s+di[A-Za-z,']{2,6}[:;,.\s’']*", plain, re.IGNORECASE)
    if word:
        close = difflib.get_close_matches(re.sub(r"[^a-z]", "", word[1].lower()), list(ORDINALS), n=1, cutoff=0.65)
        if close:
            return "district", str(ORDINALS[close[0]])
    digit = re.fullmatch(r"district\s+(?:no\.?\s*)?(\d)\s*:?", plain, re.IGNORECASE)
    if digit:
        return "district", digit[1]
    return None


def lines(all_rows: list[Row], heading=header) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    state: dict[str, object] = {
        "run": 0,
        "county": None,
        "office": None,
        "district": None,
        "seen": set(),
        "opening": False,
    }
    group: list[dict[str, object]] = []
    ids = [0]
    pending: list[Word] = []
    pending_number: tuple[int, bool, list[Word]] | None = None

    def close(total: int | None, party: str | None) -> None:
        nonlocal group
        mine = [entry for entry in group if party is None or entry["party"] in (None, party, "r&d")]
        for entry in mine:
            entry["printed"] = total
            entry["total_party"] = party
            entry["group"] = ids[0]
        ids[0] += 1
        group = [entry for entry in group if entry not in mine] if total is not None else []

    def record(row: Row, left: list[Word], value: tuple[int, bool, list[Word]]) -> None:
        text = re.sub(r"\s+", " ", " ".join(word.text for word in left)).strip(" .,-")
        text = re.sub(r"(?:\s*\.){2,}", "", text).strip(" .,-")
        votes, clean, taken = value
        boxed = [*left, *taken]
        if (
            state["run"] == 0
            and len(taken) == 2
            and taken[0].text == "1"
            and len(taken[1].text) < 3
            and re.search(r"[A-Za-z][.,]$", " ".join(word.text for word in left).strip())
        ):
            votes, clean, _ = number(taken[1:]) or value
            text += " l" if votes != value[0] else ""
        total = re.fullmatch(r"(?:\W*\w{0,2}\s*)?otal\W*vote\W*(.*)", text, re.IGNORECASE)
        if total:
            letters = re.sub(r"[^a-z]", "", total[1].lower())
            close(votes, letters[:1] if letters[:1] in ("r", "d", "l") else None)
            return
        if re.search(r"(?i)\bmanual\b", text):
            return
        if state["office"] is None and state["opening"] and not text.isupper():
            state.update(office="sheriff", opening=False)
        if state["office"] is None or (text.isupper() and "," not in text):
            return
        scatter = re.fullmatch(r"scatter(?:ing)?s?", text, re.IGNORECASE)
        party = None
        town = None
        name = text
        writein = bool(re.search(r"write-?in", text, re.IGNORECASE))
        if not scatter:
            found = PARTY.search(text)
            if found:
                party = found[2] or ("l" if found[1] == "1" else found[1].lower())
                party = "r&d" if party == "d&r" else party
                name = text[: found.start()]
            name = re.sub(r"\(write-?in\)", "", name, flags=re.IGNORECASE).strip(" ,")
        district = state["district"]
        if not scatter:
            prefix = re.match(r"dist(?:rict|\.)?\s*(?:no\.?\s*)?(\d)\s+(.*)", name, re.IGNORECASE)
            if prefix and re.match(r"dist", prefix[2], re.IGNORECASE):
                return
            if prefix:
                district, name = prefix[1], prefix[2]
                state.update(office="board of commissioners", district=district)
            name = re.sub(r"(,\s*(?:Jr|Sr|II|III))\.*\s+(?=[A-Z])", r"\1., ", name)
            parts = [part.strip() for part in name.split(",")]
            if len(parts) > 1 and not re.fullmatch(r"(?:jr|sr|st|ii|iii|iv|2d|3d)\.?", parts[-1], re.IGNORECASE):
                town = parts[-1]
                name = ", ".join(parts[:-1])
        if not name:
            return
        entry = {
            "run": state["run"],
            "county": state["county"],
            "office": state["office"],
            "district": district,
            "name": "Scatter" if scatter else name,
            "town": town,
            "party": party,
            "writein": writein,
            "votes": votes,
            "clean": clean,
            "digits": " ".join(word.text for word in taken),
            "page": row.page,
            "x0": min(w.x0 for w in boxed) / row.width,
            "y0": min(w.y0 for w in boxed) / row.height,
            "x1": max(w.x1 for w in boxed) / row.width,
            "y1": max(w.y1 for w in boxed) / row.height,
            "printed": None,
            "total_party": None,
            "group": None,
            "tail": None,
        }
        out.append(entry)
        group.append(entry)

    last_page = 0
    tailable: dict[str, object] | None = None
    for row in all_rows:
        prior, tailable = tailable, None
        before = len(out)
        if last_page and row.page - last_page > 10:
            state.update(office=None, district=None, opening=False)
        last_page = row.page
        found = number(row.words)
        if found is None and len(row.words) > 2 and re.fullmatch(r"[Il|]{3}", row.words[-1].text):
            if re.search(
                r"(?i)otal\W*vote\W*[rdl1]?\W*$", " ".join(word.text for word in row.words[:-1]).replace(".", "")
            ):
                found = (111, False, row.words[-1:])
        left = (
            [word for word in row.words[: row.words.index(found[2][0])] if not LEADER.fullmatch(word.text)]
            if found
            else row.words
        )
        left_text = " ".join(word.text for word in left)
        kind = heading(left_text) if left and not found else None
        if kind is not None:
            pending = []
            pending_number = None
            close(None, None)
            if kind[0] == "county":
                if kind[1] in state["seen"] and kind[1] == COUNTIES[0] and state["county"] != kind[1]:
                    state["run"] = int(state["run"]) + 1
                    state["seen"] = set()
                state["seen"].add(kind[1])
                state.update(county=kind[1], office=None, district=None, opening=True)
            elif kind[0] == "office":
                state.update(office=kind[1], district=None, opening=False)
            elif kind[0] == "district":
                repeat = kind[1] == state["district"] and any(
                    entry["county"] == state["county"]
                    and entry["office"] == state["office"]
                    and entry["run"] == state["run"]
                    and entry["district"] == kind[1]
                    for entry in out
                )
                state["district"] = str(int(kind[1]) + 1) if repeat else kind[1]
            else:
                state.update(office=None, district=None, opening=False)
            continue
        if found and left:
            if pending and not re.search(r"[A-Za-z]{3}", left_text):
                left = pending + left
            pending = []
            pending_number = None
            record(row, left, found)
        elif found:
            if pending:
                record(row, pending, found)
                pending = []
            else:
                pending_number = found
        elif left and re.search(r"[A-Za-z]{3}", left_text) and state["office"] is not None:
            if (
                prior is not None
                and cut_short(prior)
                and re.fullmatch(TAIL, left_text.strip(" .,-"))
                and not left_text.startswith("Total")
            ):
                prior["tail"] = left_text.strip(" .,-")
                prior["x1"] = max(float(prior["x1"]), max(w.x1 for w in left) / row.width)
                prior["y1"] = max(float(prior["y1"]), max(w.y1 for w in left) / row.height)
            elif pending_number is not None:
                record(row, left, pending_number)
                pending_number = None
            else:
                pending = left
        if len(out) > before:
            tailable = out[-1]
    return out


SUFFIX = re.compile(r"(?:Jr|Sr|St|II|III|IV)\.?", re.IGNORECASE)
HEAD_TOWN = re.compile(r"(.+ [A-Z]\.?) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)")
INITIAL_SURNAME = re.compile(r"([A-Z]\.?) ([A-Z][a-z]+)")


def cut_short(entry: dict[str, object]) -> bool:
    name = re.sub(r"[, ]+(?:Jr|Sr|II|III|IV)\.?$", "", str(entry["name"]))
    return (
        entry["town"] is None
        and name != "Scatter"
        and bool(
            re.fullmatch(r"(?i)[rdl]|r&d|d&r|[a-z]+|.*(?:^| )[A-Z]\.?", name)
            or HEAD_TOWN.fullmatch(name)
            or INITIAL_SURNAME.fullmatch(name)
        )
    )


def joined(name: str, town: str | None, tail: str) -> tuple[str, str | None]:
    parts = [
        part.strip()
        for part in tail.split(",")
        if SUFFIX.fullmatch(part.strip()) or len(re.sub(r"[^A-Za-z]", "", part)) >= 3
    ]
    tail_town = None
    if len(parts) > 1 and not SUFFIX.fullmatch(parts[-1]):
        tail_town = parts.pop()
    words = " ".join(parts).replace(",", " ").split()
    suffix = words.pop() if len(words) > 1 and SUFFIX.fullmatch(words[-1]) else None
    held = re.search(r"[, ]+(Jr|Sr|St|II|III|IV)\.?(?= |$)", name)
    if held:
        suffix, name = suffix or held[1], name[: held.start()] + name[held.end() :]
    if suffix:
        suffix = {"st": "Sr.", "sr": "Sr.", "jr": "Jr."}.get(suffix.lower().rstrip("."), suffix)
    if re.fullmatch(r"(?i)[rdl]|r&d|d&r", name):
        name = ""
    name = re.sub(r"(^| )([A-Z])$", r"\1\2.", name)
    head = HEAD_TOWN.fullmatch(name)
    initial = INITIAL_SURNAME.fullmatch(name)
    if re.fullmatch(r"[A-Z]\.", name) and len(words) == 2:
        name, town = f"{words[0]} {name} {words[1]}", tail_town or town
    elif name in TOWNS and len(words) == 2:
        name, town = " ".join(words), name
    elif initial and initial[2] in TOWNS and len(words) == 2 and tail_town is None:
        name, town = f"{words[0]} {initial[1]} {words[1]}", initial[2]
    elif initial and initial[2] not in TOWNS and len(words) == 2 and words[1] in TOWNS and tail_town is None:
        name, town = f"{words[0]} {initial[1]} {initial[2]}", words[1]
    elif initial and tail_town and len(words) == 1:
        name, town = f"{words[0]} {initial[1]} {initial[2]}", tail_town
    elif head and len(words) == 1 and tail_town is None:
        last_is_town, word_is_town = head[2] in TOWNS, words[0] in TOWNS
        if last_is_town and not word_is_town:
            name, town = f"{head[1]} {words[0]}", head[2]
        elif word_is_town and not last_is_town:
            town = words[0]
        else:
            name = f"{name} {words[0]} ?"
    elif head:
        name = f"{name} {' '.join(words)} ?"
    else:
        name, town = f"{name} {' '.join(words)}".strip(), tail_town or town
    return (f"{name}, {suffix}" if suffix else name), town


TOWNS: set[str] = set()


def link(year: int, kind: str) -> str:
    return UNH_2008 if year == 2008 else ITEM.format(item=ITEMS[year], kind=kind)


def pdf(year: int) -> Path:
    if year == 2008:
        return raw.cached(SOURCE, UNH_2008).path
    for kind in ("_bw", ""):
        try:
            return raw.cached(SOURCE, link(year, kind)).path
        except LookupError:
            continue
    raise LookupError(str(year))


def fetch() -> None:
    for year in years():
        try:
            pdf(year)
            continue
        except LookupError:
            pass
        for kind in ("_bw", ""):
            try:
                raw.fetch(SOURCE, link(year, kind), suffix=".pdf")
                break
            except Exception as error:
                print(f"{year}{kind}: {error!r}")
            if year == 2008:
                break
    if (PARSED / "lines.parquet").exists():
        read()


def parse() -> None:
    frames = []
    for year in years():
        try:
            path = pdf(year)
        except LookupError:
            print(f"{year}: not fetched")
            continue
        pages = candidate_pages(path)
        if not pages:
            print(f"{year}: no county office pages")
            continue
        found = {number: page for number, page in words(path, pages[0], pages[-1]).items() if number in pages}
        frame = pl.DataFrame(lines(rows(found)), infer_schema_length=None, schema_overrides={"tail": pl.String})
        runs = frame["run"].n_unique()
        print(f"{year}: {frame.height} lines, {runs} runs")
        frames.append(
            frame.with_columns(
                year=pl.lit(year), stage=pl.col("run").replace_strict({0: "primary", 1: "general"}, default="other")
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).with_columns(ocr=pl.lit(False)).write_parquet(PARSED / "lines.parquet")
    supplement()


OCR_YEARS = range(1970, 1999, 2)
OCR_DPI = 300


def ocr_words(path: Path, sha: str, number: int) -> tuple[float, float, list[Word]]:
    folder = PAGES / "ocr"
    cache = folder / f"{sha}-{number}.tsv"
    folder.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        stem = folder / f".{sha}-{number}"
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                str(OCR_DPI),
                "-gray",
                "-png",
                "-singlefile",
                "-f",
                str(number),
                "-l",
                str(number),
                str(path),
                str(stem),
            ],
            check=True,
        )
        image = stem.with_suffix(".png")
        cache.write_text(
            subprocess.run(
                ["tesseract", str(image), "-", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
            ).stdout
        )
        image.unlink()
    width = height = 0.0
    found: list[Word] = []
    for line in cache.read_text().splitlines()[1:]:
        cells = line.split("\t")
        if len(cells) < 12:
            continue
        left, top, wide, high = (float(cell) for cell in cells[6:10])
        if cells[0] == "1":
            width, height = wide, high
        elif cells[0] == "5" and cells[11].strip():
            found.append(Word(cells[11].strip(), left, top, left + wide, top + high))
    return width, height, found


def clusters(pages: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for page in sorted(pages):
        if out and page - out[-1][-1] <= 6:
            out[-1].append(page)
        else:
            out.append([page])
    return out


def supplement() -> None:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    if "ocr" in frame.columns:
        frame = frame.filter(~pl.col("ocr"))
    keys = ["year", "stage", "county", "office", "district", "side"]
    known = blocks(frame.filter(pl.col("county").is_not_null())).select(keys).unique()
    extra = []
    for year in OCR_YEARS:
        have = frame.filter(pl.col("year") == year)
        if have.is_empty():
            continue
        path = pdf(year)
        sha = path.stem
        for cluster in clusters(have["page"].unique().to_list()):
            stage = have.filter(pl.col("page").is_in(cluster))["stage"].mode()[0]
            span = range(cluster[0] - 2, cluster[-1] + 3)
            pages = {number: ocr_words(path, sha, number) for number in span}
            found = pl.DataFrame(
                [line for line in lines(rows(pages)) if line["votes"] < 10**9], infer_schema_length=None
            )
            if found.is_empty():
                continue
            new = blocks(
                found.filter(pl.col("county").is_not_null()).with_columns(year=pl.lit(year), stage=pl.lit(stage))
            ).join(known, on=keys, how="anti", nulls_equal=True)
            extra.append(
                new.drop("added", "agrees", "side", "digits", "printed_right", strict=False).with_columns(
                    ocr=pl.lit(True)
                )
            )
    base = frame.with_columns(ocr=pl.lit(False))
    pl.concat([base, *extra], how="diagonal_relaxed").write_parquet(PARSED / "lines.parquet")


PAGES = DATA / "pages" / SOURCE
DIGITIZED = 8
DERIVED = 1


def verified(frame: pl.DataFrame) -> pl.DataFrame:
    sums = (
        frame.filter(pl.col("group").is_not_null())
        .group_by("year", "stage", "group")
        .agg(pl.col("votes").sum().alias("added"), pl.col("printed").first())
    )
    return frame.join(sums, on=["year", "stage", "group"], how="left").with_columns(
        agrees=pl.col("added") == pl.col("printed")
    )


def blocks(frame: pl.DataFrame) -> pl.DataFrame:
    frame = verified(frame).with_columns(
        side=pl.when(pl.col("stage") == "general")
        .then(pl.lit(None, pl.String))
        .otherwise(
            pl.when(pl.col("party").is_in(["r", "d", "l"]))
            .then(pl.col("party"))
            .otherwise(pl.when(pl.col("total_party").is_in(["r", "d", "l"])).then(pl.col("total_party")))
        )
    )
    return frame.filter(~((pl.col("stage") == "primary") & pl.col("side").is_null()))


def render(path: Path, sha: str, number: int) -> tuple[Path, int, int]:
    from PIL import Image

    PAGES.mkdir(parents=True, exist_ok=True)
    image = PAGES / f"{sha}-{number}.png"
    if not image.exists():
        stem = PAGES / f".{sha}-{number}"
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "100",
                "-png",
                "-singlefile",
                "-f",
                str(number),
                "-l",
                str(number),
                str(path),
                str(stem),
            ],
            check=True,
        )
        stem.with_suffix(".png").replace(image)
    with Image.open(image) as opened:
        return image, *opened.size


READ_DPI = 150
REREAD_DPI = 200
READ_PROMPT = (
    "This is a page of New Hampshire county election returns. Each numbered row below names a candidate line on the page "
    "(county, office, district, candidate, town, party, and its vertical position as a fraction of the page height). "
    "For each row, read the vote count printed on that candidate's line in the page image. Reply with only a JSON object "
    "mapping each row number to the integer printed, or to null if the line is not on the page or the number cannot "
    "be read. Do not add or correct numbers."
)


@functools.cache
def page_words(year: int, page: int) -> list[tuple[float, str]]:
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf(year)), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    height = float(re.search(r'<page width="[\d.]+" height="([\d.]+)"', out)[1])
    return [(float(top) / height, text) for top, text in re.findall(r'yMin="([\d.]+)"[^>]*>([^<]*)<', out)]


def cleared(frame: pl.DataFrame) -> pl.DataFrame:
    sums = (
        frame.filter(pl.col("group").is_not_null() & pl.col("printed").is_not_null())
        .group_by("year", "stage", "group")
        .agg(
            pl.col("votes").sum().alias("added"),
            pl.col("printed").first(),
            pl.col("page").n_unique().alias("pages"),
            pl.col("page").first(),
            pl.col("y1").max().alias("bottom"),
        )
        .filter((pl.col("added") != pl.col("printed")) & (pl.col("pages") == 1))
    )
    loose = [
        (row["year"], row["stage"], row["group"])
        for row in sums.iter_rows(named=True)
        if not any(
            row["bottom"] - 0.002 <= top <= row["bottom"] + 0.04 and re.search(r"ota|Vote", text)
            for top, text in page_words(int(row["year"]), int(row["page"]))
        )
    ]
    if not loose:
        return frame
    marked = pl.DataFrame(loose, schema=["year", "stage", "group"], orient="row").with_columns(
        pl.col("year").cast(frame.schema["year"]), pl.col("group").cast(frame.schema["group"]), loose=pl.lit(True)
    )
    return (
        frame.join(marked, on=["year", "stage", "group"], how="left")
        .with_columns(
            printed=pl.when(pl.col("loose").is_not_null()).then(None).otherwise(pl.col("printed")),
            total_party=pl.when(pl.col("loose").is_not_null()).then(None).otherwise(pl.col("total_party")),
        )
        .drop("loose")
    )


def held(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        blocks(cleared(frame.filter(pl.col("county").is_not_null())))
        .filter(pl.col("agrees").is_null())
        .sort("year", "page", "y0", "x0")
        .with_columns(rid=pl.int_range(pl.len()).over("year", "page"))
    )


def replies(rows: pl.DataFrame, prefix: str, year: int, page: int) -> dict[str, int | None] | None:
    import json

    try:
        text = "".join(
            block.get("text", "")
            for block in json.loads(raw.cached(SOURCE, read_key(prefix, year, page, listed(rows))).path.read_text())[
                "content"
            ]
        )
    except LookupError:
        return None
    found = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(found[0]) if found else {}
    except ValueError:
        return None


BLOCK = ["year", "stage", "county", "office", "district", "side"]


def disputed(frame: pl.DataFrame) -> pl.DataFrame:
    parts = []
    for (year, page), rows in held(frame).group_by("year", "page", maintain_order=True):
        second = replies(rows, "read", int(year), int(page))
        if second is None:
            continue
        rows = rows.with_columns(
            miss=pl.Series(
                [
                    not (clean and second.get(str(rid)) == votes)
                    for rid, clean, votes in zip(rows["rid"], rows["clean"], rows["votes"], strict=True)
                ]
            ),
            second=pl.Series([second.get(str(rid)) for rid in rows["rid"]], dtype=pl.Int64),
        )
        parts.append(rows.filter(pl.col("miss")).drop("rid"))
    if not parts:
        return held(frame).clear()
    return (
        pl.concat(parts).sort("year", "page", "y0", "x0").with_columns(rid=pl.int_range(pl.len()).over("year", "page"))
    )


def twice(raw_lines: pl.DataFrame) -> pl.DataFrame:
    agreed: list[tuple[int, int, float, float, int]] = []
    for (year, page), rows in held(raw_lines).group_by("year", "page", maintain_order=True):
        second = replies(rows, "read", int(year), int(page))
        if second is None:
            continue
        for row in rows.iter_rows(named=True):
            if row["clean"] and second.get(str(row["rid"])) == row["votes"]:
                agreed.append((year, page, row["y0"], row["x0"], row["votes"]))
    for (year, page), rows in disputed(raw_lines).group_by("year", "page", maintain_order=True):
        third = replies(rows, "read3", int(year), int(page))
        if third is None:
            continue
        for row in rows.iter_rows(named=True):
            value = third.get(str(row["rid"]))
            if value is not None and value in (row["votes"], row["second"]):
                agreed.append((year, page, row["y0"], row["x0"], value))
    return pl.DataFrame(
        agreed,
        schema={"year": pl.Int64, "page": pl.Int64, "y0": pl.Float64, "x0": pl.Float64, "read": pl.Int64},
        orient="row",
    ).with_columns(twice=pl.lit(True))


def missed(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        blocks(frame.filter(pl.col("county").is_not_null()))
        .filter(~pl.col("agrees"))
        .sort("year", "page", "y0", "x0")
        .with_columns(rid=pl.int_range(pl.len()).over("year", "page"))
    )


def reread_votes(raw_lines: pl.DataFrame) -> pl.DataFrame:
    rows = missed(raw_lines)
    values: dict[tuple[int, int, int], int | None] = {}
    for (year, page), page_rows in rows.group_by("year", "page", maintain_order=True):
        reply = replies(page_rows, "reread", int(year), int(page))
        for rid in page_rows["rid"]:
            value = (reply or {}).get(str(rid))
            values[(int(year), int(page), int(rid))] = value if isinstance(value, int) else None
    rows = rows.with_columns(
        read=pl.Series(
            [values[(int(y), int(p), int(r))] for y, p, r in zip(rows["year"], rows["page"], rows["rid"], strict=True)],
            dtype=pl.Int64,
        )
    )
    return (
        rows.with_columns(
            sure=pl.col("read").is_not_null().all().over("year", "stage", "group")
            & (pl.col("read").sum().over("year", "stage", "group") == pl.col("printed"))
        )
        .filter(pl.col("sure"))
        .select("year", "page", "y0", "x0", "read")
        .with_columns(twice=pl.lit(True), summed=pl.lit(True))
    )


NAMES_PROMPT = (
    "This is a page of New Hampshire county election returns. Each numbered row below is a candidate line on the page "
    "(county, office, district, the name and town as an earlier text extraction gave them, party, and its vertical "
    "position as a fraction of the page height). The extraction may have dropped part of the name, run the name into "
    "the town, or garbled letters. For each row, copy the candidate's name and town exactly as printed on that line "
    "of the page image: full given names, initials, surname, and suffix, without correcting spelling. Reply with only "
    'a JSON object mapping each row number to {"name": "...", "town": "..." or null}, or to null if the line is not '
    "on the page or cannot be read. Do not guess a name that is not printed."
)


def named(frame: pl.DataFrame) -> pl.DataFrame:
    wanted = pl.read_csv(
        Path(__file__).with_name("nh_manual_names.tsv"),
        separator="\t",
        schema_overrides={"district": pl.String, "side": pl.String},
    ).with_columns(pl.col("district").fill_null(""), pl.col("side").fill_null(""))
    rows = (
        blocks(frame.filter(pl.col("county").is_not_null()))
        .with_columns(want_district=pl.col("district").fill_null(""), want_side=pl.col("side").fill_null(""))
        .join(
            wanted.rename({"district": "want_district", "side": "want_side"}),
            on=["year", "county", "office", "want_side"],
        )
        .filter((pl.col("want_district") == pl.col("want_district_right")) | (pl.col("want_district") == ""))
    )
    return (
        rows.sort("year", "page", "y0", "x0")
        .unique(["year", "page", "y0", "x0"], keep="first", maintain_order=True)
        .with_columns(rid=pl.int_range(pl.len()).over("year", "page"))
    )


def name_reads(raw_lines: pl.DataFrame) -> dict[tuple[int, int, float, float], tuple[str, str | None]]:
    found: dict[tuple[int, int, float, float], tuple[str, str | None]] = {}
    for (year, page), rows in named(raw_lines).group_by("year", "page", maintain_order=True):
        reply = replies(rows, "names", int(year), int(page))
        for row in rows.iter_rows(named=True):
            read = (reply or {}).get(str(row["rid"]))
            if isinstance(read, dict) and isinstance(read.get("name"), str) and read["name"].strip():
                town = read.get("town")
                found[(int(year), int(page), row["y0"], row["x0"])] = (
                    read["name"].strip(),
                    town.strip() if isinstance(town, str) and town.strip() else None,
                )
    return found


def names() -> None:
    request(named(pl.read_parquet(PARSED / "lines.parquet")), "county_names", "names", REREAD_DPI)


def read() -> None:
    request(held(pl.read_parquet(PARSED / "lines.parquet")), "county_pages", "read", READ_DPI)


def third() -> None:
    request(disputed(pl.read_parquet(PARSED / "lines.parquet")), "county_third", "read3", REREAD_DPI)


def reread() -> None:
    request(missed(pl.read_parquet(PARSED / "lines.parquet")), "county_sums", "reread", REREAD_DPI)


def listed(rows: pl.DataFrame) -> str:
    return "\n".join(
        f"{row['rid']}: {row['county']} | {row['office']} | {row['district'] or ''} | {row['name']}"
        f"{', ' + row['town'] if row['town'] else ''} | {row['party'] or ''} | y={row['y0']:.3f}"
        for row in rows.iter_rows(named=True)
    )


def read_key(prefix: str, year: int, page: int, listing: str) -> str:
    if prefix not in ("read", "read3", "names"):
        return f"{prefix}:{year}:{page}"
    return f"{prefix}:{year}:{page}:{hashlib.sha1(listing.encode()).hexdigest()[:8]}"


def request(frame: pl.DataFrame, job: str, prefix: str, dpi: int) -> None:
    import base64

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    folder = DATA / "tmp" / SOURCE / str(dpi)
    folder.mkdir(parents=True, exist_ok=True)
    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for (year, page), rows in frame.group_by("year", "page", maintain_order=True):
        image = folder / f"{year}-{page}.png"
        if not image.exists():
            stem = folder / f".{year}-{page}"
            subprocess.run(
                ["pdftoppm", "-r", str(dpi), "-png", "-singlefile", "-f", str(page), "-l", str(page)]
                + [str(pdf(int(year))), str(stem)],
                check=True,
            )
            stem.with_suffix(".png").replace(image)
        listing = listed(rows)
        params = MessageCreateParamsNonStreaming(
            model="claude-haiku-5-5",
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(image.read_bytes()).decode(),
                            },
                        },
                        {
                            "type": "text",
                            "text": f"{NAMES_PROMPT if prefix == 'names' else READ_PROMPT}\n\nRows:\n{listing}",
                        },
                    ],
                }
            ],
        )
        wanted[read_key(prefix, int(year), int(page), listing)] = (
            params,
            (3500 * price_in + (200 + 12 * rows.height) * price_out) / 1e6,
        )
    print(batch.ensure(SOURCE, job, wanted)[1])


SPELLINGS = {
    "Cheryl A. Bums": "Cheryl A. Burns",
    "William D. Paine U": "William D. Paine II",
    "William D. Paine H": "William D. Paine II",
    "Peter R. Lovely, St.": "Peter R. Lovely, Sr.",
}


def repair(
    name: str, town: str | None, party: str | None, tail: str | None = None
) -> tuple[str, str | None, str | None]:
    if tail:
        name, town = joined(name, town, tail)
    name = re.sub(r"\b([A-Z]), (?=[A-Z][a-z])", r"\1. ", name)
    name = SPELLINGS.get(name, name)
    name = re.sub(r"[, ]+11$", " II", name)
    name = re.sub(r"[, ]+(?:111|HI|IH)$", " III", name)
    found = PERIOD_TOWN.fullmatch(name) if town is None else None
    if found:
        return found[1] + (f", {found[2]}" if found[2] else ""), found[3], party
    found = STRANDED.fullmatch(name) if town is None else None
    if found and not re.fullmatch(r"(?:Jr|Sr|II|III|IV)", found[2]):
        return found[1], found[2], party or found[3]
    found = TRAILING.fullmatch(name)
    if found:
        name, party = found[1], party or found[2]
    found = re.fullmatch(r"(.+?),\s*([A-Z][a-z]+)", name)
    if found and not re.fullmatch(r"(?:Jr|Sr|II|III|IV)", found[2]):
        return found[1], town or found[2], party
    return name, town, party


def person(name: str) -> tuple[str, str]:
    cleaned = re.sub(r"[.,]", " ", name)
    words = [word for word in cleaned.split() if not re.fullmatch(r"(?i)jr|sr|st|ii|iii|iv|2d|3d", word)]
    return (re.sub(r"[^a-z]", "", words[-1].lower()), words[0][:1].lower()) if words else ("", "")


def given_surname(name: str) -> frozenset[str]:
    words = re.sub(r"[.,]", " ", name).split()
    return frozenset(
        re.sub(r"[^a-z-]", "", word.lower())
        for word in words
        if len(word) > 1 and not re.fullmatch(r"(?i)jr|sr|st|ii|iii|iv|2d|3d", word)
    )


def same_person(option: str, name: str) -> bool:
    ours, theirs = given_surname(option), given_surname(name)
    if ours and ours == theirs:
        return True
    last = re.sub(r"[^a-z-]", "", re.sub(r"[.,]", " ", name).split()[-1].lower()) if name.split() else ""
    return len(ours) == 1 and len(next(iter(ours))) >= 6 and len(theirs) > 1 and distance(next(iter(ours)), last) <= 1


def garbled(name: str) -> bool:
    return any(
        re.search(r"[a-z][A-Z]", word) and not re.match(r"(?:Mc|Mac|De|Di|Du|Le|La|O')[A-Z]", word)
        for word in name.split()
    )


def distance(left: str, right: str) -> int:
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        last, row[0] = row[0], i
        for j, b in enumerate(right, 1):
            last, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, last + (a != b))
    return row[-1]


def misread(option: str, name: str) -> bool:
    ours, theirs = person(option), person(name)
    if ours[1] != theirs[1] or len(theirs[0]) < 4 or len(ours[0]) < 4:
        return False
    first = [re.sub(r"[^a-z]", "", word.lower()) for word in (option.split()[0], name.split()[0])]
    return distance(ours[0], theirs[0]) <= (1 if min(len(ours[0]), len(theirs[0])) < 6 else 2) and (
        first[0] == first[1] or len(first[0]) > 3 and distance(first[0], first[1]) <= 1
    )


def page_lines(raw_lines: pl.DataFrame) -> pl.DataFrame:
    added = pl.read_csv(
        Path(__file__).with_name("nh_manual_lines.tsv"), separator="\t", schema_overrides={"beside_y0": pl.Float64}
    )
    parts = []
    for row in added.iter_rows(named=True):
        beside = raw_lines.filter(
            (pl.col("year") == row["year"])
            & (pl.col("page") == row["page"])
            & ((pl.col("y0") - row["beside_y0"]).abs() < 1e-5)
        ).head(1)
        parts.append(
            beside.with_columns(
                name=pl.lit(row["name"]),
                town=pl.lit(row["town"]),
                votes=pl.lit(row["votes"], dtype=pl.Int64),
                clean=pl.lit(True),
                digits=pl.lit(f"{row['votes']:,}"),
                x0=pl.lit(row["x0"]),
                y0=pl.lit(row["y0"]),
                x1=pl.lit(row["x1"]),
                y1=pl.lit(row["y1"]),
                writein=pl.lit(False),
                tail=pl.lit(None, dtype=pl.String),
                party=pl.lit(None, dtype=pl.String),
            )
        )
    return pl.concat(parts)


@functools.cache
def aliases() -> list[dict[str, str]]:
    frame = pl.read_csv(Path(__file__).with_name("nh_manual_aliases.tsv"), separator="\t", infer_schema=False)
    return [{key: value or "" for key, value in row.items()} for row in frame.iter_rows(named=True)]


def amended(lines: pl.DataFrame) -> pl.DataFrame:
    fixes = pl.read_csv(
        Path(__file__).with_name("nh_manual_fixes.tsv"),
        separator="\t",
        schema_overrides={"beside_y0": pl.Float64, "votes": pl.Int64, "name": pl.String, "town": pl.String},
    )
    lines = lines.with_row_index("ix")
    lines = lines.filter(
        ~pl.col("ix").is_in(
            lines.filter(pl.col("district").is_null())
            .join(
                lines.filter(pl.col("district").is_not_null()).select(
                    "year", "page", "county", "office", "stage", "votes", "side", pl.col("y0").alias("y0b")
                ),
                on=["year", "page", "county", "office", "stage", "votes", "side"],
                nulls_equal=True,
            )
            .filter((pl.col("y0") - pl.col("y0b")).abs() < 0.01)["ix"]
            .implode()
        )
    ).drop("ix")
    touched: list[tuple[int, str, int]] = []

    def find(frame: pl.DataFrame, year: int, page: int, y0: float) -> pl.DataFrame:
        return frame.filter(
            (pl.col("year") == year) & (pl.col("page") == page) & ((pl.col("y0") - y0).abs() < 6e-5)
        ).head(1)

    for fix in fixes.iter_rows(named=True):
        year, page, y0 = fix["year"], fix["page"], fix["y0"]
        if fix["op"] == "add":
            beside = find(lines, year, page, fix["beside_y0"])
            assert beside.height == 1, fix
            row = beside.with_columns(
                name=pl.lit(fix["name"]),
                town=pl.lit(fix["town"]),
                votes=pl.lit(fix["votes"], dtype=pl.Int64),
                clean=pl.lit(True),
                digits=pl.lit(f"{fix['votes']:,}"),
                y0=pl.lit(y0),
                y1=pl.col("y1") + (y0 - fix["beside_y0"]),
                writein=pl.lit(False),
                tail=pl.lit(None, dtype=pl.String),
                party=pl.lit(None, dtype=pl.String),
                twice=pl.lit(True),
            )
            lines = pl.concat([lines, row.select(lines.columns)])
            hit = beside
        else:
            hit = find(lines, year, page, y0)
            assert hit.height == 1, fix
            if fix["op"] == "drop":
                lines = lines.filter(
                    ~((pl.col("year") == year) & (pl.col("page") == page) & ((pl.col("y0") - y0).abs() < 6e-5))
                )
            elif fix["op"] == "rename":
                at = pl.col("page").eq(page) & pl.col("year").eq(year) & ((pl.col("y0") - y0).abs() < 6e-5)
                lines = lines.with_columns(
                    name=pl.when(at).then(pl.lit(fix["name"])).otherwise(pl.col("name")),
                    town=pl.when(at).then(pl.lit(fix["town"])).otherwise(pl.col("town")),
                )
            elif fix["op"] == "relabel":
                at = pl.col("page").eq(page) & pl.col("year").eq(year) & ((pl.col("y0") - y0).abs() < 6e-5)
                moved = 1_000_000 + hit["group"][0]
                lines = lines.with_columns(
                    side=pl.when(at).then(pl.lit(fix["name"])).otherwise(pl.col("side")),
                    group=pl.when(at).then(pl.lit(moved, dtype=pl.Int64)).otherwise(pl.col("group")),
                    printed=pl.when(at).then(pl.lit(fix["votes"], dtype=pl.Int64)).otherwise(pl.col("printed")),
                )
                touched.append((year, hit["stage"][0], moved))
            elif fix["op"] == "read":
                lines = lines.with_columns(
                    agrees=pl.when(
                        (pl.col("year") == year) & (pl.col("page") == page) & ((pl.col("y0") - y0).abs() < 6e-5)
                    )
                    .then(True)
                    .otherwise(pl.col("agrees")),
                    twice=pl.col("twice")
                    | ((pl.col("year") == year) & (pl.col("page") == page) & ((pl.col("y0") - y0).abs() < 6e-5)),
                )
                continue
            elif fix["op"] == "votes":
                lines = lines.with_columns(
                    votes=pl.when(
                        (pl.col("year") == year) & (pl.col("page") == page) & ((pl.col("y0") - y0).abs() < 6e-5)
                    )
                    .then(fix["votes"])
                    .otherwise(pl.col("votes"))
                )
            else:
                lines = lines.with_columns(
                    printed=pl.when(
                        (pl.col("year") == year)
                        & (pl.col("stage") == hit["stage"][0])
                        & (pl.col("group") == hit["group"][0])
                    )
                    .then(fix["votes"])
                    .otherwise(pl.col("printed"))
                )
        touched.append((year, hit["stage"][0], hit["group"][0]))
    if touched:
        marked = (
            pl.DataFrame(touched, schema=["year", "stage", "group"], orient="row")
            .unique()
            .with_columns(
                pl.col("year").cast(lines.schema["year"]),
                pl.col("group").cast(lines.schema["group"]),
                touched=pl.lit(True),
            )
        )
        lines = (
            lines.join(marked, on=["year", "stage", "group"], how="left")
            .with_columns(added=pl.col("votes").sum().over("year", "stage", "group"))
            .with_columns(
                agrees=pl.when(pl.col("touched").is_not_null())
                .then(pl.col("added") == pl.col("printed"))
                .otherwise(pl.col("agrees"))
            )
            .drop("touched")
        )
    return lines


def emit() -> Path:
    from ea.changeset import Changeset
    from ea.sources.us.nh_sos_county import COLUMNS, CONTEST, TEMPLATE, WRITE_IN
    from ea.store import connect

    raw_lines = pl.read_parquet(PARSED / "lines.parquet").filter(pl.col("county").is_not_null())
    raw_lines = pl.concat([raw_lines, page_lines(raw_lines)], how="diagonal_relaxed")
    reads = name_reads(raw_lines)
    if reads:
        raw_lines = (
            raw_lines.join(
                pl.DataFrame(
                    [(*key, *value) for key, value in reads.items()],
                    schema={
                        "year": pl.Int32,
                        "page": pl.Int64,
                        "y0": pl.Float64,
                        "x0": pl.Float64,
                        "read_name": pl.String,
                        "read_town": pl.String,
                    },
                    orient="row",
                ),
                on=["year", "page", "y0", "x0"],
                how="left",
            )
            .with_columns(
                name=pl.coalesce("read_name", "name"),
                town=pl.when(pl.col("read_name").is_not_null()).then(pl.col("read_town")).otherwise(pl.col("town")),
                tail=pl.when(pl.col("read_name").is_not_null()).then(None).otherwise(pl.col("tail")),
            )
            .drop("read_name", "read_town")
        )
    TOWNS.update(
        raw_lines.filter(pl.col("tail").is_null() & pl.col("town").str.contains(r"^[A-Z][a-z]+(?: [A-Z][a-z]+)?$"))[
            "town"
        ].unique()
    )
    fixes = {
        key: repair(*key)
        for key in raw_lines.select("name", "town", "party", "tail").unique().iter_rows()
        if key[0] != "Scatter"
    }
    lines = blocks(
        raw_lines.with_columns(
            pl.struct("name", "town", "party", "tail")
            .map_elements(
                lambda row: dict(
                    zip(
                        ("name", "town", "party"),
                        fixes.get((row["name"], row["town"], row["party"], row["tail"]), tuple(row.values())[:3]),
                        strict=True,
                    )
                ),
                return_dtype=pl.Struct({"name": pl.String, "town": pl.String, "party": pl.String}),
            )
            .alias("fixed")
        )
        .drop("name", "town", "party", "tail")
        .unnest("fixed")
        .with_columns(
            name=pl.when(pl.col("writein") & pl.col("name").str.contains(r"^(?:\S+|[A-Z]\.? \S+)$"))
            .then(pl.lit("Scatter"))
            .otherwise(pl.col("name"))
        )
        .pipe(cleared)
    )
    both = pl.concat([twice(raw_lines), reread_votes(raw_lines)], how="diagonal_relaxed").unique(
        ["year", "page", "y0", "x0"], keep="last"
    )
    lines = (
        lines.join(both, on=["year", "page", "y0", "x0"], how="left")
        .with_columns(
            pl.col("twice").fill_null(False),
            pl.col("summed").fill_null(False),
            votes=pl.coalesce("read", "votes"),
        )
        .drop("read")
        .pipe(amended)
        .with_columns(
            agrees=pl.when(
                pl.col("agrees").is_null()
                & pl.col("twice").all().over("year", "stage", "county", "office", "district", "side")
            )
            .then(True)
            .when(pl.col("summed").all().over("year", "stage", "county", "office", "district", "side"))
            .then(True)
            .otherwise(pl.col("agrees"))
        )
    )
    only = os.environ.get("NH_YEARS")
    if only:
        lines = lines.filter(pl.col("year").is_in([int(year) for year in only.split(",")]))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's county offices, 1970-2014, by county, read from the Secretary of State's Manual for the General Court",
            polity="US-NH",
        )
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '33%%'"""
            ).fetchall()
        }
        contests: dict[tuple[int, str, str, str | None, str | None], list[tuple[str, str]]] = {}
        for value, contest, text in conn.execute(
            """select x.value, x.entity_id, n.text from external_id x join name n on n.entity_id = x.entity_id
               where x.scheme = 'us.contest' and x.value ~ '(^|:)NH:' and n.text like '%% County %% election%%'"""
        ).fetchall():
            found = CONTEST.match(text)
            if found is None:
                continue
            year, county, name, district, party = found.groups()
            contests.setdefault(
                (int(year), county.lower(), name.lower(), district, party.lower() if party else None), []
            ).append((value, contest))
        templates: dict[tuple[str, str, str | None], dict[str, object]] = {}
        scopes: dict[str, object] = {}
        days: dict[tuple[int, str, bool], tuple[object, str]] = {}
        columns = ", ".join(f"c.{column}" for column in TEMPLATE)
        for text, election, path, *rest in conn.execute(
            f"""select n.text, c.election_id, s.path, {columns} from external_id x join contest c on c.id = x.entity_id
                join name n on n.entity_id = c.id and n.is_preferred join slug s on s.entity_id = c.id
                where x.scheme = 'us.contest' and x.value ~ '(^|:)NH:' and n.text like '%% County %% election%%'"""
        ).fetchall():
            found = CONTEST.match(text)
            if found is None:
                continue
            year, county, name, district, party = found.groups()
            row = dict(zip(TEMPLATE, rest, strict=True))
            templates.setdefault((county.lower(), name.lower(), district), row)
            days.setdefault((int(year), county.lower(), party is not None), (election, path.split("/")[3][:10]))
            if party and row["party_scope_id"] is not None:
                scopes.setdefault(party.lower(), row["party_scope_id"])
        taken = {
            row[0] for row in conn.execute("select path from slug where path like 'us/nh/%%-county/%%'").fetchall()
        }
        sources: dict[int, tuple[object, int, Path, str]] = {}
        for year in sorted(lines["year"].unique()):
            path = pdf(int(year))
            artifact = raw.cached(SOURCE, UNH_2008) if year == 2008 else None
            if artifact is None:
                for kind in ("_bw", ""):
                    try:
                        artifact = raw.cached(SOURCE, link(int(year), kind))
                        break
                    except LookupError:
                        continue
            assert artifact is not None
            title = f"Manual for the General Court, {int(year) + 1}"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://scholars.unh.edu/court/61/"
                if year == 2008
                else f"https://archive.org/details/{ITEMS[int(year)]}",
                license="Public domain",
            )
            sources[int(year)] = (source, cs.artifact(artifact, source), path, artifact.sha256)
        pages: dict[tuple[int, int], tuple[object, int, int]] = {}
        report: dict[str, list[str]] = {}
        values = []
        keyed = lines.group_by("year", "stage", "county", "office", "district", "side", maintain_order=True)
        for (year, stage, county, office, district, side), rows in keyed:
            party = {"r": "republican", "d": "democratic", "l": "libertarian"}.get(str(side)) if side else None
            tag = f"{year} {county} {office} {district or ''} {party or stage}".replace("  ", " ")
            key = (int(year), str(county).lower(), str(office), district, party)
            candidates = contests.get(key) or (
                contests.get((*key[:3], "1", key[4]))
                if office == "board of commissioners" and district is None
                else None
            )
            minted = None
            if not candidates:
                lone = (
                    district is None
                    and key[2] == "board of commissioners"
                    and {k[3] for k in contests if k[:3] == key[:3]} == {"1"}
                )
                district = "1" if lone else district
                template = templates.get((key[1], key[2], district))
                day = days.get((key[0], key[1], party is not None))
                if key[2] == "board of commissioners" and district is None or template is None or day is None:
                    report.setdefault("no contest and no sibling to copy from", []).append(tag)
                    continue
                template = dict(template)
                template["party_scope_id"] = scopes.get(party) if party else None
                if party and template["party_scope_id"] is None:
                    report.setdefault("no contest and no party scope", []).append(tag)
                    continue
                minted = (template, *day)
                external = (
                    f"{day[1]}:NH:{key[1]}:{slug(TITLES[key[2]])}"
                    + (f"-{district}" if district else "")
                    + (f"-{party}" if party else "")
                )
                contest = None
            elif len(candidates) != 1:
                report.setdefault("two contests", []).append(f"{tag} {[c[0] for c in candidates]}")
                continue
            else:
                external, contest = candidates[0]
            if (
                minted is None
                and conn.execute(
                    """select 1 from result_set rs join changeset c on c.id = rs.changeset_id
                   where rs.contest_id = %s and rs.is_default and rs.status = 'active'""",
                    [contest],
                ).fetchone()
            ):
                continue
            if minted is not None:
                election, default, first, options = minted[1], None, 1, []
            else:
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
            if rows.filter(pl.col("agrees").is_null()).height:
                report.setdefault("no printed total", []).append(f"{tag} {external}")
                continue
            if not rows["agrees"].all():
                report.setdefault("lines do not add to the printed total", []).append(f"{tag} {external}")
                continue
            if rows["twice"].all() and rows["printed"].is_null().all():
                report.setdefault("checked by two reads", []).append(f"{tag} {external}")
            overrides = [
                alias
                for alias in aliases()
                if (alias["year"], alias["county"], alias["office"], alias["side"])
                == (str(year), str(county), str(office), str(side or ""))
            ]
            for alias in overrides:
                if alias["action"] == "drop":
                    rows = rows.filter(pl.col("name") != alias["printed"])
                elif alias["action"] == "rename":
                    rows = rows.with_columns(
                        pl.when(pl.col("name") == alias["printed"])
                        .then(pl.lit(alias["name"]))
                        .otherwise(pl.col("name"))
                        .alias("name")
                    )
            forced = {alias["printed"] for alias in overrides if alias["action"] == "new"}
            accepted = any(alias["action"] == "accept" for alias in overrides)
            totals = rows.group_by("name", maintain_order=True).agg(pl.col("votes").sum())
            others = [option[0] for option in options if option[2] == "others"]
            if not totals["votes"].sum():
                report.setdefault("no votes", []).append(f"{tag} {external}")
                continue
            matched: dict[str, str] = {}
            fresh: list[str] = []
            failed = None
            for name in totals["name"]:
                if name == "Scatter" or WRITE_IN.search(name):
                    if len(others) > 1:
                        failed = f"{name}: no single others option"
                        break
                    if others:
                        matched[name] = others[0]
                    else:
                        fresh.append(name)
                    continue
                if name in forced:
                    fresh.append(name)
                    continue
                mine = person(name)
                hits = [
                    option
                    for option in options
                    if option[2] == "candidate"
                    and (
                        re.match(rf"{re.escape(option[1])}[.,]? +[A-Z][A-Za-z. ]+$", name)
                        or person(option[1])[0] == mine[0]
                        or len(mine[0]) > 4
                        and difflib.SequenceMatcher(None, person(option[1])[0], mine[0]).ratio() >= 0.8
                        and person(option[1])[1] == mine[1]
                        or misread(option[1], name)
                    )
                ]
                exact = [option for option in options if option[2] == "candidate" and same_person(option[1], name)]
                if len(exact) > 1:
                    exact = [option for option in exact if option[1] == name] or exact
                if len(exact) == 1:
                    hits = exact
                if len(hits) > 1:
                    hits = [hit for hit in hits if person(hit[1])[1] == mine[1]] or hits
                if len(hits) > 1:
                    hits = [hit for hit in hits if hit[3]]
                if (
                    not hits
                    and CLEAN.fullmatch(name)
                    and not garbled(name)
                    and not re.search(r"[a-z]\. [A-Za-z]|- ", name)
                    and len(mine[0]) > 2
                    and (len(name.split()) > 2 or len(name.split()[0]) > 2)
                ):
                    fresh.append(name)
                    continue
                if len(hits) != 1:
                    failed = f"{name} matches {[hit[1] for hit in hits]}"
                    break
                matched[name] = hits[0][0]
            named = [name for name in totals["name"] if name != "Scatter" and not WRITE_IN.search(name)]
            surnames = [person(name)[0] for name in named]
            if not failed and any(
                person(name)[0] != "" and surnames.count(person(name)[0]) > 1 for name in fresh if name in named
            ):
                failed = "a new name shares a surname with another line"
            if (
                not failed
                and not accepted
                and fresh
                and not [m for m in matched.values() if m not in others]
                and any(option[2] == "candidate" for option in options)
            ):
                failed = f"no line matches the contest's options: {[option[1] for option in options]}"
            if failed:
                report.setdefault("candidate mismatch", []).append(
                    f"{tag} {external}: {failed} | {list(zip(totals['name'], rows.group_by('name', maintain_order=True).agg(pl.col('town').first())['town']))}"
                )
                continue
            if minted is not None:
                title = TITLES[str(office)]
                text = f"{year} {str(county).title()} County {title} election" + (
                    f", District {district}" if district else ""
                )
                text += f", {party.title()} primary" if party else ""
                base = (
                    f"us/nh/{str(county).lower()}-county/{minted[2]}-{'primary' if party else 'general'}/{slug(title)}"
                    + (f"-district-{district}" if district else "")
                    + (f"-{party}" if party else "")
                )
                path = base if base not in taken else f"{base}-2"
                taken.add(path)
                contest = cs.key("us.contest", external)
                cs.add("contest", id=contest, election_id=election, **minted[0])
                cs.add("name", entity_id=contest, text=text, lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=path, entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=sources[int(year)][0])
                report.setdefault("contest added from the manual", []).append(tag)
            for offset, name in enumerate(fresh):
                option = cs.key("us.option", f"{external}:{slug(name)}")
                if name == "Scatter" or WRITE_IN.search(name):
                    cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Others")
                    others.append(str(option))
                    matched[name] = str(option)
                    continue
                human = cs.key(f"{SOURCE}.person", f"{external}:{slug(name)}")
                cs.add("person", id=human)
                cs.add("name", entity_id=human, text=name, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=len(options) + offset + 1,
                    ballot_label=name,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{external}:{slug(name)}:head"),
                    person_id=human,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    is_independent=False,
                )
                matched[name] = str(option)
                report.setdefault("option added from the manual", []).append(f"{tag} {name}")
            ours: dict[str, int] = {}
            for name, votes in totals.iter_rows():
                ours[matched[name]] = ours.get(matched[name], 0) + votes
            theirs = {option[0]: option[3] for option in options if option[3]}
            if default is not None and any(ours.get(o, 0) != theirs.get(o, 0) for o in {*ours, *theirs}):
                gap = {
                    label: (theirs.get(oid, 0), ours.get(oid, 0))
                    for oid, label, _, _ in options
                    if theirs.get(oid, 0) != ours.get(oid, 0)
                }
                report.setdefault("differs from the compilation", []).append(f"{tag} {external} {gap}")
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
            source, artifact_id, path, sha = sources[int(year)]
            if default is not None:
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
            ranked = sorted(((votes, option) for option, votes in ours.items() if option not in others), reverse=True)
            if winners and (
                len(winners) > len(ranked)
                or {option for votes, option in ranked if votes >= ranked[len(winners) - 1][0]} != winners
            ):
                report.setdefault("declared winner is not the top vote-getter, outcomes left off", []).append(
                    f"{tag} {external}"
                )
                declared = []
            for option, kind, seats in declared:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            unit = cs.unit("us.county", counties[re.sub(r"[^a-z]", "", str(county).lower())])
            for option, votes in ours.items():
                if not votes:
                    continue
                mine = rows.filter(pl.col("name").is_in([name for name, hit in matched.items() if hit == option]))
                first_page = int(mine["page"][0])
                mine = mine.filter(pl.col("page") == first_page)
                if (int(year), first_page) not in pages:
                    image, width, height = render(path, sha, first_page)
                    page = cs.page(artifact_id, first_page)
                    pages[(int(year), first_page)] = (page, width, height)
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifact_id,
                        page_number=first_page,
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
                page = pages[(int(year), first_page)][0]
                flags = DIGITIZED | (
                    DERIVED
                    if mine.height > 1 or rows.filter(pl.col("name").is_in(list(matched))).height > len(matched)
                    else 0
                )
                values.append(
                    pl.DataFrame(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": option,
                            "channel": 0,
                            "measure": 1,
                            "count_round": first,
                            "flags": flags,
                            "value": votes,
                            "page_id": str(page),
                            "x0": float(mine["x0"].min()),
                            "y0": float(mine["y0"].min()),
                            "x1": float(mine["x1"].max()),
                            "y1": float(mine["y1"].max()),
                        },
                        schema_overrides={
                            "channel": pl.Int16,
                            "measure": pl.Int16,
                            "count_round": pl.Int16,
                            "flags": pl.Int16,
                        },
                    )
                )
        for reason, items in report.items():
            print(f"{len(items)}: {reason}")
            for item in items[: int(os.environ.get("NH_SHOW", "25"))]:
                print("   ", item)
        print(f"{len(values)} values in {len(cs.rows.get('result_set', [])) // 2} sets")
        if values:
            frame = pl.concat(values)
            cs.frame("result_value", frame.drop("page_id", "x0", "y0", "x1", "y1"))
            cs.frame(
                "value_locator",
                frame.select(
                    "result_set_id",
                    "unit_id",
                    "option_id",
                    "channel",
                    "measure",
                    "count_round",
                    "page_id",
                    "x0",
                    "y0",
                    "x1",
                    "y1",
                ).with_columns(source_id=pl.lit(None, pl.String)),
            )
        return cs.write()
