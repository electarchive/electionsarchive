import difflib
import itertools
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import polars as pl

from ea import raw
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
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
NOISE = re.compile(r"[\^_'•■*]")
LEADER = re.compile(r"^[.\-—•\s]+$")
PARTY = re.compile(r",\s*(r&d|r|d|i|ind|l|lib)\s*\.?\s*(?:\(write-?in\))?$", re.IGNORECASE)


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
        and re.search(r"[A-Z]{4,} COUNTY|COUNTY OFFICES", text)
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
        if re.fullmatch(r"[\d,.\^_'•■*]+", word.text) and re.search(r"\d", word.text):
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
    county = re.fullmatch(rf"({'|'.join(COUNTIES)}) COUNTY:?", plain, re.IGNORECASE)
    if county:
        return "county", county[1].upper()
    found = re.match(r"For\s+(?:the\s+)?(.*?)[:;.]?$", plain, re.IGNORECASE)
    if found:
        for pattern, name in OFFICES:
            if re.fullmatch(pattern, found[1].strip(), re.IGNORECASE):
                return "office", name
        return "other", plain
    word = re.fullmatch(r"(first|second|third|fourth|fifth)\s+district:?", plain, re.IGNORECASE)
    if word:
        return "district", str(ORDINALS[word[1].lower()])
    digit = re.fullmatch(r"district\s+(?:no\.?\s*)?(\d)\s*:?", plain, re.IGNORECASE)
    if digit:
        return "district", digit[1]
    return None


def lines(all_rows: list[Row]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    state: dict[str, object] = {"run": 0, "county": None, "office": None, "district": None, "seen": set()}
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
        total = re.fullmatch(r"total\s+vote\s*,?\s*(.*)", text, re.IGNORECASE)
        if total:
            party = total[1].strip(" .,").lower() or None
            close(votes, party[:1] if party else None)
            return
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
                party = found[1].lower()
                name = text[: found.start()]
            name = re.sub(r"\(write-?in\)", "", name, flags=re.IGNORECASE).strip(" ,")
        district = state["district"]
        if not scatter:
            prefix = re.match(r"dist(?:rict|\.)?\s*(?:no\.?\s*)?(\d)\s+(.*)", name, re.IGNORECASE)
            if prefix:
                district, name = prefix[1], prefix[2]
                state.update(office="board of commissioners", district=district)
            parts = [part.strip() for part in name.split(",")]
            if len(parts) > 1 and not re.fullmatch(r"(?:jr|sr|ii|iii|iv|2d|3d)\.?", parts[-1], re.IGNORECASE):
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
            "page": row.page,
            "x0": min(w.x0 for w in boxed) / row.width,
            "y0": min(w.y0 for w in boxed) / row.height,
            "x1": max(w.x1 for w in boxed) / row.width,
            "y1": max(w.y1 for w in boxed) / row.height,
            "printed": None,
            "total_party": None,
            "group": None,
        }
        out.append(entry)
        group.append(entry)

    for row in all_rows:
        found = number(row.words)
        left = (
            [word for word in row.words[: row.words.index(found[2][0])] if not LEADER.fullmatch(word.text)]
            if found
            else row.words
        )
        left_text = " ".join(word.text for word in left)
        kind = header(left_text) if left and not found else None
        if kind is not None:
            pending = []
            pending_number = None
            close(None, None)
            if kind[0] == "county":
                if kind[1] in state["seen"] and kind[1] == COUNTIES[0] and state["county"] != kind[1]:
                    state["run"] = int(state["run"]) + 1
                    state["seen"] = set()
                state["seen"].add(kind[1])
                state.update(county=kind[1], office=None, district=None)
            elif kind[0] == "office":
                state.update(office=kind[1], district=None)
            elif kind[0] == "district":
                state["district"] = kind[1]
            else:
                state.update(office=None, district=None)
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
            if pending_number is not None:
                record(row, left, pending_number)
                pending_number = None
            else:
                pending = left
    return out


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
        frame = pl.DataFrame(lines(rows(found)))
        runs = frame["run"].n_unique()
        print(f"{year}: {frame.height} lines, {runs} runs")
        frames.append(
            frame.with_columns(
                year=pl.lit(year), stage=pl.col("run").replace_strict({0: "primary", 1: "general"}, default="other")
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(PARSED / "lines.parquet")


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
            pl.when(pl.col("party").is_in(["r", "d"]))
            .then(pl.col("party"))
            .otherwise(pl.when(pl.col("total_party").is_in(["r", "d"])).then(pl.col("total_party")))
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
READ_PROMPT = (
    "This is a page of New Hampshire county election returns. Each numbered row below names a candidate line on the page "
    "(county, office, district, candidate, town, party, and its vertical position as a fraction of the page height). "
    "For each row, read the vote count printed on that candidate's line in the page image. Reply with only a JSON object "
    "mapping each row number to the integer printed, or to null if the line is not on the page or the number cannot "
    "be read. Do not add or correct numbers."
)


def held(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        blocks(frame.filter(pl.col("county").is_not_null()))
        .filter(pl.col("agrees").is_null())
        .sort("year", "page", "y0", "x0")
        .with_columns(rid=pl.int_range(pl.len()).over("year", "page"))
    )


def read() -> None:
    import base64

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    frame = held(pl.read_parquet(PARSED / "lines.parquet"))
    folder = DATA / "tmp" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for (year, page), rows in frame.group_by("year", "page", maintain_order=True):
        image = folder / f"{year}-{page}.png"
        if not image.exists():
            stem = folder / f".{year}-{page}"
            subprocess.run(
                ["pdftoppm", "-r", str(READ_DPI), "-png", "-singlefile", "-f", str(page), "-l", str(page)]
                + [str(pdf(int(year))), str(stem)],
                check=True,
            )
            stem.with_suffix(".png").replace(image)
        listing = "\n".join(
            f"{row['rid']}: {row['county']} | {row['office']} | {row['district'] or ''} | {row['name']}"
            f"{', ' + row['town'] if row['town'] else ''} | {row['party'] or ''} | y={row['y0']:.3f}"
            for row in rows.iter_rows(named=True)
        )
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
                        {"type": "text", "text": f"{READ_PROMPT}\n\nRows:\n{listing}"},
                    ],
                }
            ],
        )
        wanted[f"read:{year}:{page}"] = (params, (3500 * price_in + (200 + 12 * rows.height) * price_out) / 1e6)
    print(batch.ensure(SOURCE, "county_pages", wanted)[1])


def person(name: str) -> tuple[str, str]:
    cleaned = re.sub(r"[.,]", " ", name)
    words = [word for word in cleaned.split() if not re.fullmatch(r"(?i)jr|sr|ii|iii|iv|2d|3d", word)]
    return (re.sub(r"[^a-z]", "", words[-1].lower()), words[0][:1].lower()) if words else ("", "")


def emit() -> Path:
    from ea.changeset import Changeset
    from ea.sources.us.nh_sos_county import COLUMNS, CONTEST, WRITE_IN
    from ea.store import connect

    lines = blocks(pl.read_parquet(PARSED / "lines.parquet").filter(pl.col("county").is_not_null()))
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
               where x.scheme = 'us.contest' and x.value like 'NH:local:%%' and n.text like '%% County %% election%%'"""
        ).fetchall():
            found = CONTEST.match(text)
            if found is None:
                continue
            year, county, name, district, party = found.groups()
            contests.setdefault(
                (int(year), county.lower(), name.lower(), district, party.lower() if party else None), []
            ).append((value, contest))
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
            party = {"r": "republican", "d": "democratic"}.get(str(side)) if side else None
            tag = f"{year} {county} {office} {district or ''} {party or stage}".replace("  ", " ")
            key = (int(year), str(county).lower(), str(office), district, party)
            candidates = contests.get(key)
            if not candidates:
                report.setdefault("no contest", []).append(tag)
                continue
            if len(candidates) != 1:
                report.setdefault("two contests", []).append(f"{tag} {[c[0] for c in candidates]}")
                continue
            external, contest = candidates[0]
            row = conn.execute(
                """select rs.election_id, rs.id, min(v.count_round), rs.source_id
                   from result_set rs join result_value v on v.result_set_k = rs.k
                   where rs.contest_id = %s and rs.is_default and rs.status = 'active' group by 1, 2, 4""",
                [contest],
            ).fetchone()
            if row is None:
                report.setdefault("no default set", []).append(f"{tag} {external}")
                continue
            election, default, first, _ = row
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
            totals = rows.group_by("name", maintain_order=True).agg(pl.col("votes").sum())
            others = [option[0] for option in options if option[2] == "others"]
            matched: dict[str, str] = {}
            failed = None
            for name in totals["name"]:
                if name == "Scatter" or WRITE_IN.search(name):
                    if len(others) != 1:
                        failed = f"{name}: no single others option"
                        break
                    matched[name] = others[0]
                    continue
                mine = person(name)
                hits = [
                    option
                    for option in options
                    if option[2] == "candidate"
                    and (
                        person(option[1])[0] == mine[0]
                        or len(mine[0]) > 4
                        and difflib.SequenceMatcher(None, person(option[1])[0], mine[0]).ratio() >= 0.8
                        and person(option[1])[1] == mine[1]
                    )
                ]
                if len(hits) > 1:
                    hits = [hit for hit in hits if person(hit[1])[1] == mine[1]] or hits
                if len(hits) > 1:
                    hits = [hit for hit in hits if hit[3]]
                if len(hits) != 1:
                    failed = f"{name} matches {[hit[1] for hit in hits]}"
                    break
                matched[name] = hits[0][0]
            if failed:
                report.setdefault("candidate mismatch", []).append(f"{tag} {external}: {failed}")
                continue
            ours: dict[str, int] = {}
            for name, votes in totals.iter_rows():
                ours[matched[name]] = ours.get(matched[name], 0) + votes
            theirs = {option[0]: option[3] for option in options if option[3]}
            gap = {
                label: (theirs.get(oid, 0), ours.get(oid, 0))
                for oid, label, _, _ in options
                if theirs.get(oid, 0) != ours.get(oid, 0)
            }
            same = all(ours.get(option, 0) == theirs.get(option, 0) for option in {*ours, *theirs})
            if not same:
                report.setdefault("differs from the compilation", []).append(f"{tag} {external} {gap}")
            if not sum(ours.values()):
                report.setdefault("no votes", []).append(f"{tag} {external}")
                continue
            result_set = cs.key(f"{SOURCE}.result_set", external)
            old = dict(
                zip(
                    COLUMNS,
                    conn.execute(f"select {', '.join(COLUMNS)} from result_set where id = %s", [default]).fetchone(),
                    strict=True,
                )
            )
            source, artifact_id, path, sha = sources[int(year)]
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
            declared = conn.execute(
                "select option_id::text, kind, seats from outcome where result_set_id = %s", [default]
            ).fetchall()
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
            for item in items[:25]:
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
