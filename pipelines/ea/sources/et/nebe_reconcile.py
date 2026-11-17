"""Join the readings of NEBE's 2021 House constituency forms into ``nebe_hopr_2021.json``.

Three readings feed it: the Tesseract cell pass (``nebe_scan``), a first multimodal reading of every page, and a
second reading by a different model of each page where the first two disagree. A number is kept when two readings
agree; ``SETTLED`` holds the numbers a person read off the page where no two did. The box of a number is its OCR cell
when the cells were found, and otherwise the form's table area.

Run with the work folder holding ``ocr*.jsonl``, ``vision/out_*.jsonl``, and ``vision2/out_*.jsonl``:
``uv run python -m ea.sources.et.nebe_reconcile <work>``.
"""

import collections
import json
import re
import sys
from pathlib import Path

from ea.sources.et import nebe_2021 as nebe

TABLE_AREA = [0.06, 0.07, 0.94, 0.64]
# (file, page): {field: value}, read by a person from the page where the readings disagree; field is "line<i>"
# (1-9), "votes<n>" for candidate row n, or "winner".
SETTLED: dict[tuple[str, int], dict[str, int | None]] = {
    # The page holds only the lower half of the form (results, winner, remarks); its accounting lines are not printed.
    ("2021-06-21_oromia_hopr", 4): {f"line{i}": None for i in range(1, 10)},
    # Invalid ballots printed as a dash.
    ("2021-06-21_oromia_hopr", 165): {"line8": None},
}
# Forms whose winner's votes differ from the English list of winners, or that the list leaves out: the constituency's
# English name, read against the list and the form's own name.
ENGLISH: dict[tuple[str, int], str] = {
    ("2021-06-21_amhara_hopr", 37): "Dil Ybza",  # the list prints 57,167; the form 58,016
    ("2021-06-21_amhara_hopr", 75): "Werebabo",  # the list prints 45,401; the form 45,501
    ("2021-06-21_oromia_hopr", 30): "Agarfa Gasera",  # the list prints 84,935 for Kombolcha 1 as well
    ("2021-06-21_oromia_hopr", 135): "Kombolcha 1",  # the list prints 84,935; the form 90,114
    ("2021-09-30_snnp_hopr", 22): "Lemo 1",  # not on the list
    ("2021-09-30_snnp_hopr", 23): "Bule",  # not on the list
}
CAVEATS: dict[tuple[str, int], str] = {
    ("2021-06-21_oromia_hopr", 4): (
        "The scanned form holds only its results table: the header and the ballot accounting lines are missing from "
        "the Board's file, so registered voters, voters, and valid and invalid ballots are not stored."
    ),
}


def _value(digits: list[str]) -> str | None:
    found = [re.sub(r"\D", "", d) for d in digits]
    found = [d for d in found if d]
    return collections.Counter(found).most_common(1)[0][0] if found else None


def _text(value: object) -> str | None:
    return None if value is None else re.sub(r"\D", "", str(value)) or None


def _load(work: Path) -> tuple[dict, dict, dict]:
    ocr: dict[tuple[str, int], dict] = {}
    for file in sorted(work.glob("ocr*.jsonl")):
        for line in file.read_text().splitlines():
            r = json.loads(line)
            k = (r["file"], r["page"])
            if k not in ocr or (r.get("results") and not ocr[k].get("results")):
                ocr[k] = r
    readings = []
    for folder in ("vision", "vision2"):
        found: dict[tuple[str, int], dict] = {}
        for file in sorted((work / folder).glob("out_*.jsonl")):
            for line in file.read_text().splitlines():
                if line.strip():
                    v = json.loads(line)
                    m = re.match(r"(.+)__(\d+)\.jpg", v["image"])
                    assert m
                    found[(m[1], int(m[2]))] = v
        readings.append(found)
    return ocr, readings[0], readings[1]


def _pick(field: str, key: tuple[str, int], ocr: str | None, first: str | None, second: str | None) -> tuple:
    settled = SETTLED.get(key, {})
    if field in settled:
        return settled[field], "settled"
    if ocr is not None and ocr == first:
        return int(ocr), "ocr+vision"
    if second is not None and first == second:
        return int(first), "vision+vision"
    if ocr is not None and ocr == second:
        return int(ocr), "ocr+vision"
    if first is None and second is None and ocr is None:
        return None, "blank"
    return None, "open"


def main() -> None:
    work = Path(sys.argv[1])
    ocr, first, second = _load(work)
    lists = nebe.winners()
    forms, open_ = [], []
    stats: collections.Counter[str] = collections.Counter()
    for key in sorted(first):
        v1 = first[key]
        if not v1.get("form"):
            continue
        v2 = second.get(key, {}) if second.get(key, {}).get("form") else {}
        o = ocr.get(key, {})
        file, number = key
        poll, region, _ = file.split("_")
        cells = o.get("accounting") if o.get("accounting") and not o.get("template") else None
        lines, boxes = [], []
        for i in range(9):
            oc = _value(cells[i]["digits"]) if cells else None
            a = _text(v1["lines"][i]) if i < len(v1.get("lines") or []) else None
            b = _text(v2["lines"][i]) if v2 and i < len(v2.get("lines") or []) else None
            value, how = _pick(f"line{i + 1}", key, oc, a, b)
            stats[how] += 1
            if how == "open":
                open_.append((key, f"line{i + 1}", oc, a, b))
            lines.append(value)
            boxes.append(cells[i]["box"] if cells else TABLE_AREA)
        rows = o.get("results") if o.get("results") and not o.get("template") else None
        # Rows are paired by their printed position: some forms list candidates by votes, not ballot number.
        second_rows = dict(enumerate(v2.get("candidates") or [])) if v2 else {}
        filled = [row for row in rows or [] if not row.get("empty")]
        candidates = []
        for i, c in enumerate(v1.get("candidates") or []):
            n = c.get("n") or i + 1
            row = filled[i] if i < len(filled) else None
            oc = _value(row["votes"]) if row else None
            b = _text(second_rows[i]["votes"]) if i in second_rows else None
            value, how = _pick(f"votes{n}", key, oc, _text(c["votes"]), b)
            stats[how] += 1
            if how == "open":
                open_.append((key, f"votes{n}", oc, _text(c["votes"]), b))
            name, party = c.get("name") or "", c.get("party") or ""
            if row and row.get("name") and nebe.REGIONS[region][2] == "am":
                parts = re.split(r"\s*[(\[/]\s*", row["name"], maxsplit=1)
                if len(parts) == 2 and nebe.party_key(parts[1]) == nebe.party_key(party):
                    name = parts[0].strip()
            if nebe.party_key(party) is None and i in second_rows and nebe.party_key(second_rows[i].get("party") or ""):
                party = second_rows[i]["party"]
            candidates.append(
                {
                    "row": i + 1,
                    "n": n,
                    "name": " ".join(name.split()),
                    "party": " ".join(party.split()),
                    "votes": value,
                    "box": row["box"] if row else TABLE_AREA,
                }
            )
        # The winner is the candidate with the most votes: on every form the winner's printed name is that
        # candidate's, while the printed ballot number of the winner need not match the row's serial number.
        counted = [c for c in candidates if c["votes"] is not None]
        top = max(counted, key=lambda c: c["votes"]) if counted else None
        if top is not None and sum(1 for c in counted if c["votes"] == top["votes"]) > 1:
            open_.append((key, "winner", None, "tie", top["votes"]))
        winner = top["row"] if top is not None else None
        listed = lists.get((poll, region), [])
        for candidate in candidates:  # a results table that prints no parties: the winner's comes from the list
            if not candidate["party"] and candidate["row"] == winner and top is not None:
                found = [w for w in listed if w["votes"] == top["votes"]]
                if len(found) == 1:
                    candidate["party"] = str(found[0]["party"])
        if key in ENGLISH:
            english = [w for w in listed if w["constituency"] == ENGLISH[key]] or [
                {"constituency": ENGLISH[key], "name": None, "gender": None}
            ]
        else:
            english = [w for w in listed if top is not None and w["votes"] == top["votes"]]
        form = {
            "poll": poll,
            "region": region,
            "page": number,
            "size": o.get("size"),
            "constituency": v1.get("constituency") or "",
            "constituency_en": english[0]["constituency"] if len(english) == 1 else None,
            "winner_en": {"name": english[0]["name"], "gender": english[0]["gender"]}
            if len(english) == 1 and english[0]["name"]
            else None,
            "lines": lines,
            "line_boxes": boxes,
            "candidates": candidates,
            "winner": winner,
        }
        if key in CAVEATS:
            form["caveat"] = CAVEATS[key]
        forms.append(form)
    out = Path(nebe.TRANSCRIPTION)
    out.write_text(json.dumps({"forms": forms}, ensure_ascii=False, indent=0) + "\n")
    print(dict(stats))
    print(f"{len(forms)} forms, {sum(1 for f in forms if f['constituency_en'] is None)} without an English match")
    (work / "open.json").write_text(json.dumps([list(map(str, o)) for o in open_], ensure_ascii=False, indent=1))
    print(f"{len(open_)} open")


if __name__ == "__main__":
    main()
