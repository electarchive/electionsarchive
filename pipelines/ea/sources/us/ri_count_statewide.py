import json
import re
import subprocess
from collections import Counter
from datetime import date
from difflib import get_close_matches
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ri_countbook import COUNTIES
from ea.store import DATA, ROOT, connect

SOURCE = "us.ri_count_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VOLUMES = ROOT / "pipelines" / "scans" / "volumes"
YEARS = range(1952, 1990, 2)
OFFICES = "governor|lieutenant-governor|secretary-of-state|attorney-general|general-treasurer"
TOWNS = {town: fips for fips, names in COUNTIES.items() for town in names}
ROW = re.compile(r"^\W*([A-Za-z][A-Za-z .'-]*?)[\s.·_:,'\-~=]*(?:\s{2,}|\s\.)\s*(\S.*)$")
CITATION = (
    "Source: the Board of Elections' count book for the election, whose town rows add up to the printed state totals"
    " held for each candidate."
)


def volume(year: int) -> dict[str, object] | None:
    path = VOLUMES / f"ri-count-{year}.json"
    return json.loads(path.read_text()) if path.exists() else None


LOOKALIKES = str.maketrans("oOlIiSBG&Zz", "00115586822")


def tokens(rest: str, width: int | None = None) -> list[int | None]:
    parts = [t for t in rest.split() if t not in {".", "-", "–", "·", "'", ","}]
    if width is not None:
        merged: list[str] = []
        for part in parts:
            if len(parts) > width and merged and re.fullmatch(r"\d{1,2}", merged[-1]) and re.fullmatch(r"\d{3}", part):
                merged[-1] += part
            else:
                merged.append(part)
        parts = merged
    out: list[int | None] = []
    for part in parts:
        cleaned = part.translate(LOOKALIKES)
        out.append(int(re.sub(r"[,.']", "", cleaned)) if re.fullmatch(r"[\d,.']*\d[\d,.']*", cleaned) else None)
    return out


def table(text: str) -> tuple[dict[str, list[int | None]], list[int | None] | None, int]:
    raw_rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = ROW.match(line)
        if match is None:
            continue
        name = re.sub(r"\s+", " ", match.group(1)).strip().lower()
        if not re.match(r"(state )?totals?$", name):
            found = get_close_matches(name, TOWNS, n=1, cutoff=0.8)
            if not found:
                continue
            name = found[0]
        raw_rows.append((name, match.group(2)))
    width = Counter(len(tokens(rest)) for name, rest in raw_rows if name in TOWNS).most_common(1)[0][0]
    lines: dict[str, list[int | None]] = {}
    total = None
    for name, rest in raw_rows:
        values = tokens(rest, width)
        if name in TOWNS:
            lines.setdefault(name, values)
        else:
            total = values
    return lines, total, width


def parse() -> None:
    out = []
    with connect() as conn:
        for year in YEARS:
            spec = volume(year)
            if spec is None:
                continue
            day = date.fromisoformat(str(spec["day"]))
            pdf = raw.cached("us.scans", str(spec["file"]["url"])).path  # type: ignore[index]
            held: dict[int, list[tuple[str, str, str]]] = {}
            for key, label, kind, votes in conn.execute(
                """select x.value, o.ballot_label, o.kind, sum(v.value)::bigint
                   from external_id x join contest c on c.id = x.entity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 1
                   join contest_option o on o.contest_id = c.id and o.k = v.option_k
                   where x.scheme = 'us.contest' and x.value ~ %s group by 1, 2, 3""",
                [f"^{year}-11-[0-9]+:RI:({OFFICES})$"],
            ).fetchall():
                held.setdefault(int(votes), []).append((key.split(":", 2)[2], label, kind))
            needed = {
                office: {
                    label
                    for votes, rows in held.items()
                    for o, label, kind in rows
                    if o == office and kind == "candidate"
                }
                for office in {o for rows in held.values() for o, _, _ in rows}
            }
            got: dict[str, dict[str, dict[str, int]]] = {}
            for leaf in spec["leaves"]:  # type: ignore[attr-defined]
                text = subprocess.run(
                    ["pdftotext", "-layout", "-f", str(leaf), "-l", str(leaf), str(pdf), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                lines, total, width = table(text)
                if total is None or len(total) != width:
                    print(year, leaf, "no usable total row")
                    continue
                for col in range(width):
                    match = held.get(total[col] or -1) if total[col] is not None else None
                    if match is None or len({o for o, _, _ in match}) != 1 or match[0][2] != "candidate":
                        continue
                    cells = {town: v[col] for town, v in lines.items() if len(v) == width}
                    missing = [t for t in TOWNS if t not in cells or cells[t] is None]
                    known = sum(v for v in cells.values() if v is not None)
                    if len(missing) == 1:
                        cells[missing[0]] = total[col] - known
                    elif missing:
                        print(year, leaf, col, "unreadable towns", missing)
                        continue
                    if sum(cells.values()) != total[col]:  # type: ignore[arg-type]
                        print(year, leaf, col, "town sum differs from the printed total")
                        continue
                    office, label, _ = match[0]
                    for town, votes in cells.items():
                        got.setdefault(office, {}).setdefault(label, {})[TOWNS[town]] = (
                            got.get(office, {}).get(label, {}).get(TOWNS[town], 0) + votes
                        )
            for office, candidates in got.items():
                if set(candidates) != needed[office]:
                    print(year, office, "skipped: matched", sorted(candidates), "of", sorted(needed[office]))
                    continue
                print(year, office, "tied", sorted(candidates))
                for label, counties in candidates.items():
                    for code, votes in counties.items():
                        out.append(
                            {
                                "year": year,
                                "chamber": office,
                                "day": day,
                                "special": False,
                                "state": "RI",
                                "district": None,
                                "code": code,
                                "candidate": label,
                                "measure": 1,
                                "writein": False,
                                "votes": votes,
                                "kind": "county",
                                "source": f"RI{year}",
                            }
                        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(out, infer_schema_length=None).with_columns(
        year=pl.col("year").cast(pl.Int32),
        district=pl.col("district").cast(pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's statewide offices from 1952 to 1988 by county, from the Board of Elections' count books",
            polity="US-RI",
        )
        sources = {}
        for year in sorted(set(frame["year"])):
            spec = volume(year)
            source = cs.source(str(spec["publisher"]), str(spec["title"]))  # type: ignore[index]
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=spec["publisher"],  # type: ignore[index]
                title=spec["title"],  # type: ignore[index]
                url=str(spec["file"]["url"]),  # type: ignore[index]
                license="Public record",
            )
            cs.artifact(raw.cached("us.scans", str(spec["file"]["url"])), source)  # type: ignore[index]
            sources[f"RI{year}"] = source
        medsl.attach(cs, conn, frame, sources, authoritative=CITATION)
        return cs.write()
