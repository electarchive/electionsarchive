"""Join the readings of NEBE's 2021 regional council constituency forms into ``nebe_rc_2021.json``.

Three readings feed it: the Tesseract cell pass (``nebe_rc_scan``, where it finds a page's tables), a first multimodal
reading of every page, and a second, independent reading of every page by a different model. A number is kept when
two readings agree; ``SETTLED`` holds the numbers a third, focused reading of the page settled where no two did. A
form starts on a page with a header and accounting table and takes the candidate rows and winners of the pages that
continue it. Each form is matched to the Board's typed English list of council winners by its winners' votes, which
gives the constituency's English name and the winners' names in English and gender.

Run with the work folder holding ``ocr.jsonl``, ``vision/v_*.jsonl``, and ``vision2/w_*.jsonl``:
``uv run python -m ea.sources.et.nebe_rc_reconcile <work>``.
"""

import collections
import json
import re
import subprocess
import sys
from pathlib import Path

from ea import raw
from ea.sources.et import nebe_2021 as nebe
from ea.sources.et import nebe_reconcile

TABLE_AREA = [0.06, 0.07, 0.94, 0.95]
# (file, page): {field: value}, read from the page by a third, focused reading where no two readings agree; field
# is "line<i>" (1-9), "seats", or "votes<n>" for candidate row n.
SETTLED: dict[tuple[str, int], dict[str, int | None]] = {}
# (file, page) of a form: its constituency's name in the English list of winners, where the votes do not decide it.
ENGLISH: dict[tuple[str, int], str] = {
    # Not in the English list of winners; the winners are those of the form's own table.
    ("2021-06-21_gambela_rc", 26): "Jor",
}
CAVEATS: dict[tuple[str, int], str] = {
    ("2021-06-21_dire-dawa_rc", 48): (
        "The form prints 4,470 registered voters, fewer than the 4,478 voters it records; its remarks say the "
        "registered voters should read 4,694. The figures are stored as printed."
    ),
    ("2021-06-21_afar_rc", 21): (
        "The form's table of winners prints ballot numbers 11, 12, and 10 beside the names of candidates 4, 5, and 6; "
        "the elected candidates are stored by name and votes, which the Board's English list of winners confirms."
    ),
}


def _readings(work: Path, pattern: str) -> dict[tuple[str, int], dict]:
    found: dict[tuple[str, int], dict] = {}
    for file in sorted(work.glob(pattern)):
        for line in file.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                found[(record["file"], int(record["page"]))] = record
    return found


def _int(value: object) -> int | None:
    text = nebe_reconcile._text(value)
    return int(text) if text else None


def _ocr_rows(record: dict | None) -> dict[int, tuple[int | None, list[float]]]:
    """Candidate rows the OCR pass read on a page: ballot number -> (votes, box)."""
    rows: dict[int, tuple[int | None, list[float]]] = {}
    if not record:
        return rows
    tables = [record.get("results", [])] + record.get("continued", [])
    for table in tables:
        for row in table:
            if row.get("empty"):
                continue
            number = _int(nebe_reconcile._value(row.get("number", [])))
            votes = _int(nebe_reconcile._value(row.get("votes", [])))
            if number is not None and number not in rows:
                rows[number] = (votes, row["box"])
    return rows


def _agree(field: str, where: tuple[str, int], values: list[int | None], unresolved: list[str]) -> int | None:
    settled = SETTLED.get(where, {})
    if field in settled:
        return settled[field]
    counts = collections.Counter(v for v in values if v is not None)
    if counts:
        value, count = counts.most_common(1)[0]
        if count >= 2:
            return value
    if all(v is None for v in values[:2]):
        return None
    unresolved.append(f"{where[0]} p{where[1]} {field}: {values}")
    return None


def english_lists() -> dict[tuple[str, str], list[dict[str, str | int]]]:
    """The English page's typed lists of council winners, by poll and region."""
    found: dict[tuple[str, str], list[dict[str, str | int]]] = {}
    for (poll, region, body), url in nebe.WINNERS.items():
        if body != "rc":
            continue
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(nebe.SOURCE, url).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        rows = found[(poll, region)] = []
        for line in text.splitlines():
            m = re.match(r"\s*(\d+)\s+(.+?)\s{2,}(.+?)\s{2,}(.+?)\s{2,}(Male|Female)\s+([\d,]+)\s*$", line)
            if m:
                rows.append(
                    {
                        "constituency": m[2].strip(),
                        "party": m[3].strip(),
                        "name": m[4].strip(),
                        "gender": m[5].lower(),
                        "votes": int(m[6].replace(",", "")),
                    }
                )
    return found


def build(work: Path) -> tuple[list[dict], list[str]]:
    first = _readings(work, "vision/v_*.jsonl")
    second = _readings(work, "vision2/w_*.jsonl")
    ocr = _readings(work, "ocr.jsonl")
    lists = english_lists()
    unresolved: list[str] = []
    forms: list[dict] = []
    current: dict | None = None
    for where in sorted(first):
        file, number = where
        poll, region, _ = file.split("_")
        a, b = first[where], second.get(where)
        if b is None:
            unresolved.append(f"{file} p{number}: no second reading")
            continue
        if a["kind"] != b["kind"]:
            unresolved.append(f"{file} p{number}: kinds {a['kind']} / {b['kind']}")
        if a["kind"] == "form":
            o = ocr.get(where) or {}
            lines = []
            accounting = o.get("accounting") or []
            for i in range(9):
                read = _int(nebe_reconcile._value(accounting[i]["digits"])) if len(accounting) == 9 else None
                theirs_ = (b["lines"] + [None] * 9)[i]
                lines.append(_agree(f"line{i + 1}", where, [(a["lines"] + [None] * 9)[i], theirs_, read], unresolved))
            current = {
                "poll": poll,
                "region": region,
                "file": file,
                "page": number,
                "size": o.get("size", [1700, 2200]),
                "constituency": a["constituency"],
                "constituency_b": b.get("constituency"),
                "seats": _agree("seats", where, [a["seats"], b["seats"]], unresolved),
                "lines": lines,
                "line_boxes": [accounting[i]["box"] if len(accounting) == 9 else TABLE_AREA for i in range(9)],
                "candidates": [],
                "winners_a": [],
                "winners_b": [],
            }
            if where in CAVEATS:
                current["caveat"] = CAVEATS[where]
            forms.append(current)
        elif a["kind"] != "continuation":
            continue
        if current is None:
            unresolved.append(f"{file} p{number}: continuation before any form")
            continue
        rows = _ocr_rows(ocr.get(where))
        theirs = {c["n"]: c for c in b.get("candidates", [])}
        for candidate in a.get("candidates", []):
            n = candidate["n"]
            other = theirs.get(n)
            read, box = rows.get(n, (None, TABLE_AREA))
            votes = _agree(
                f"votes{n}", where, [candidate["votes"], other["votes"] if other else None, read], unresolved
            )
            if other and nebe.party_key(candidate["party"]) != nebe.party_key(other["party"]):
                unresolved.append(f"{file} p{number} party{n}: {candidate['party']} / {other['party']}")
            current["candidates"].append(
                {
                    "row": n,
                    "name": candidate["name"].strip(),
                    "party": candidate["party"].strip(),
                    "votes": votes,
                    "page": number,
                    "box": box,
                }
            )
        missing = set(theirs) - {c["n"] for c in a.get("candidates", [])}
        if missing:
            unresolved.append(f"{file} p{number}: rows only in the second reading {sorted(missing)}")
        current["winners_a"] += a.get("winners", [])
        current["winners_b"] += b.get("winners", [])
    for form in forms:
        _english(form, lists.get((form["poll"], form["region"]), []), unresolved)
    return forms, unresolved


def _english(form: dict, listed: list[dict], unresolved: list[str]) -> None:
    """Match the form to its constituency in the English list of winners by the candidates' votes.

    The elected candidates are the rows whose votes match the list's winners, preferring the rows the form's own
    winners table names; a winners table whose ballot numbers disagree with them is reported, because on some forms
    the numbers printed there do not match the names beside them.
    """
    where = f"{form['file']} p{form['page']}"
    votes = {c["row"]: c["votes"] for c in form["candidates"]}
    rows = [c["row"] for c in form["candidates"]]
    if len(set(rows)) != len(rows):
        unresolved.append(f"{where}: repeated ballot numbers {rows}")
    printed = form.pop("winners_a")
    other = form.pop("winners_b")
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for row in listed:
        groups[str(row["constituency"])].append(row)
    name = ENGLISH.get((form["file"], form["page"]))
    if name is None:
        mine = collections.Counter(v for v in votes.values() if v is not None)
        scores = {
            constituency: sum((mine & collections.Counter(int(r["votes"]) for r in rows_)).values()) / len(rows_)
            for constituency, rows_ in groups.items()
        }
        best = max(scores.items(), key=lambda item: item[1], default=(None, 0.0))
        if best[0] is None or best[1] < 0.75:
            unresolved.append(f"{where}: no constituency in the English list matches all winners ({best})")
            return
        name = best[0]
    form["constituency_en"] = name
    if name not in groups:
        if sorted(printed) != sorted(other) or not printed:
            unresolved.append(f"{where}: not in the English list, and winners {printed} / {other}")
        form["winners"] = sorted(printed)
        form["winners_en"] = {}
        return
    group = groups[name]
    if form["seats"] is not None and len(group) != form["seats"]:
        unresolved.append(f"{where}: {form['seats']} seats but {len(group)} winners listed for {name}")
    english: dict[str, dict[str, str]] = {}
    left = list(rows)
    unlabeled = []
    for row in group:
        match = sorted((n for n in left if votes[n] == row["votes"]), key=lambda n: n not in printed)
        if match:
            english[str(match[0])] = {"name": str(row["name"]), "gender": str(row["gender"])}
            left.remove(match[0])
            candidate = next(c for c in form["candidates"] if c["row"] == match[0])
            if not candidate["party"]:
                candidate["party"] = str(row["party"])
                unlabeled.append(match[0])
        else:
            # The list's figure differs from the form's: take the form's own winners table for this winner.
            spare = [n for n in left if n in printed and n not in english and votes[n] is not None]
            near = sorted(spare, key=lambda n: abs(votes[n] - int(row["votes"])))
            if near and abs(votes[near[0]] - int(row["votes"])) <= 10:
                n = near[0]
                english[str(n)] = {"name": str(row["name"]), "gender": str(row["gender"])}
                left.remove(n)
                note = (
                    f"The Board's English list of winners prints {int(row['votes']):,} votes for {row['name']}; "
                    f"the form prints {votes[n]:,}, which is stored."
                )
                form["caveat"] = f"{form['caveat']} {note}" if form.get("caveat") else note
            else:
                unresolved.append(f"{where}: English winner {row['name']} ({row['votes']}) not on the form")
    if unlabeled:
        note = (
            "The form's results table prints no party beside the candidates; the parties of the elected candidates "
            "are taken from the Board's English list of winners."
        )
        form["caveat"] = f"{form['caveat']} {note}" if form.get("caveat") else note
    form["winners"] = sorted(int(n) for n in english)
    if sorted(printed) != form["winners"] or sorted(other) != form["winners"]:
        form["winners_table"] = {"first": printed, "second": other}
    form["winners_en"] = english


def main() -> None:
    work = Path(sys.argv[1])
    forms, unresolved = build(work)
    kept = []
    for form in forms:
        pages = {form["page"], *(c["page"] for c in form["candidates"])}
        blocked = [line for line in unresolved for page in pages if re.match(f"{form['file']} p{page}[ :]", line)]
        if blocked or "constituency_en" not in form or form["seats"] is None:
            print(f"left out {form['file']} p{form['page']} {form['constituency']}", file=sys.stderr)
            continue
        kept.append(form)
    forms = kept
    for form in forms:
        form.pop("constituency_b", None)
        form.pop("file", None)
    for line in unresolved:
        print(line)
    print(f"{len(forms)} forms, {len(unresolved)} unresolved", file=sys.stderr)
    out = Path(nebe.__file__).with_name("nebe_rc_2021.json")
    out.write_text(json.dumps({"forms": forms}, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
