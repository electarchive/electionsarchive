import html
import re
import statistics
import subprocess
from statistics import median

ORDINALS = [
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
    "Sixth",
    "Seventh",
    "Eighth",
    "Ninth",
    "Tenth",
    "Eleventh",
    "Twelfth",
    "Thirteenth",
    "Fourteenth",
    "Fifteenth",
    "Sixteenth",
    "Seventeenth",
    "Eighteenth",
    "Nineteenth",
    "Twentieth",
    "Twenty-first",
    "Twenty-second",
    "Twenty-third",
    "Twenty-fourth",
    "Twenty-fifth",
]
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
NUMBER = re.compile(r"^\d[\d,]*$")
LABELS = {
    "ELECTORATE": "electorate",
    "PAPERS": "invalid",
    "POLL": "valid",
    "SEATS": "seats",
    "QUOTA": "quota",
}

type Word = tuple[float, float, float, float, str]


def number(text: str) -> int:
    return int(text.replace(",", ""))


def read_pages(pdf: str) -> list[list[Word]]:
    chunks = subprocess.run(["pdftotext", "-bbox", pdf, "-"], check=True, capture_output=True, text=True).stdout.split(
        "<page "
    )[1:]
    pages: list[list[Word]] = []
    for chunk in chunks:
        raw = [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(chunk)]
        long = [w for w in raw if len(w[4]) >= 5]
        rotated = bool(long) and median(w[3] - w[1] for w in long) > median(w[2] - w[0] for w in long)
        if not rotated:
            pages.append(raw)
            continue
        turned = [(-d, a, -b, c, t) for a, b, c, d, t in raw]
        marks = [(w[0], w[4]) for w in turned if w[4] in ORDINALS]
        if len(marks) >= 2:
            order = {n: ORDINALS.index(n) for _, n in marks}
            first, last = min(marks, key=lambda m: order[m[1]]), max(marks, key=lambda m: order[m[1]])
            if first[0] > last[0]:
                turned = [(b, -c, d, -a, t) for a, b, c, d, t in raw]
        pages.append(turned)
    return pages


def read_lines(words: list[Word]) -> list[tuple[float, list[Word]]]:
    lines: list[tuple[float, list[Word]]] = []
    for word in sorted(words, key=lambda w: (w[1], w[0])):
        if lines and abs(lines[-1][0] - word[1]) < 3:
            lines[-1][1].append(word)
        else:
            lines.append((word[1], [word]))
    return [(y, sorted(ws)) for y, ws in lines]


def read_book(pdf: str) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for page_number, words in enumerate(read_pages(pdf), 1):
        lines = read_lines(words)
        header = next(
            (i for i, (_, ws) in enumerate(lines) if sum(w[4] in ORDINALS for w in ws) >= 1 and len({w[4] for w in ws}) >= 1),
            None,
        )
        if header is None:
            continue
        counts = sorted((w for w in lines[header][1] if w[4] in ORDINALS), key=lambda w: w[0])
        if not counts:
            continue
        centers = [((w[0] + w[2]) / 2, ORDINALS.index(w[4]) + 1) for w in counts]
        pitch = statistics.median(b[0] - a[0] for a, b in zip(centers, centers[1:])) if len(centers) > 1 else 60.0
        start = centers[0][0] - pitch / 2
        head = [ws for _, ws in lines[:header]]
        text = " ".join(" ".join(w[4] for w in ws) for ws in head)
        is_first = "QUOTA" in text and "ELECTORATE" in text
        if is_first:
            values: dict[str, int] = {}
            label_x = 0.0
            elected: list[str] = []
            for ws in head:
                tokens = [w[4] for w in ws]
                for index, word in enumerate(ws):
                    key = LABELS.get(word[4])
                    if key is None or key in values:
                        continue
                    after = next((v for v in ws[index + 1 :] if NUMBER.match(v[4])), None)
                    if after is not None:
                        values[key] = number(after[4])
                        label_x = max(label_x, after[2])
                        names = [v[4] for v in ws if v[0] > after[2] + 1]
                        if names:
                            elected.append(" ".join(names))
                _ = tokens
            left = []
            for ws in head:
                line_text = " ".join(w[4] for w in ws)
                if "GENERAL" in line_text and "ELECTION" in line_text:
                    continue
                label = next((w for w in ws if w[4] in ("TOTAL", "INVALID", "VALID", "NUMBER", "QUOTA")), None)
                limit = label[0] - 5 if label else 1e9
                left.extend(
                    w[4]
                    for w in ws
                    if w[2] < limit and w[4] not in ("CONSTITUENCY", "OF", "NAMES", "CANDIDATES", "CANDIDATE(S)", "ELECTED")
                )
            name = re.sub(r"\s*-\s*", "-", " ".join(left)).strip()
            current = {
                "constituency": name,
                "elected": elected,
                "labels": [],
                "rows": [],
                "nt": {},
                "total": {},
                **values,
                "page": page_number,
            }
            results.append(current)
        if current is None or (not is_first and page_number != current["pages"][-1] + 1):
            continue
        body: list[tuple[str, dict[int, int], bool]] = []
        for _, ws in lines[header + 1 :]:
            label_words = [w for w in ws if w[2] < start - 4]
            cell_words = [w for w in ws if w[2] >= start - 4]
            label = " ".join(w[4] for w in label_words if not NUMBER.match(w[4]) or len(label_words) > 1)
            label = re.sub(r"^\d+\s+", "", label).strip()
            cells: dict[int, int] = {}
            signed = False
            for index, word in enumerate(cell_words):
                if word[4] == "+":
                    signed = True
                    continue
                follower = cell_words[index + 1] if index + 1 < len(cell_words) else None
                if word[4] == "-" and follower is not None and NUMBER.match(follower[4]) and follower[0] - word[2] < 12:
                    signed = True
                    continue
                if NUMBER.match(word[4]) or word[4] == "-":
                    previous = cell_words[index - 1] if index else None
                    if previous is not None and previous[4] in ("+", "-") and word[0] - previous[2] < 12:
                        continue
                    center = (word[0] + word[2]) / 2
                    count = min(centers, key=lambda c: abs(c[0] - center))
                    if abs(count[0] - center) > pitch * 0.45:
                        continue
                    cells[count[1]] = 0 if word[4] == "-" else number(word[4])
            if label or cells:
                body.append((label, cells, signed))
        current.setdefault("pages", []).append(page_number)
        current.setdefault("body", []).append(body)
    return results


def assemble(book: dict[str, object]) -> dict[str, object]:
    names: list[str] = []
    series: list[dict[int, int]] = []
    nt: dict[int, int] = {}
    total: dict[int, int] = {}
    for index, body in enumerate(book["body"]):
        rows: list[dict[int, int]] = []
        started = index > 0
        finished = False
        awaiting = False
        for label, cells, signed in body:
            upper = label.upper()
            if upper.startswith("NON-TRANSFERABLE"):
                if cells and not signed:
                    nt.update(cells)
                    finished = True
                else:
                    awaiting = True
                continue
            if awaiting and not label and cells and not signed:
                nt.update(cells)
                awaiting = False
                finished = True
                continue
            if label == "TOTAL" or upper == "TOTAL":
                total.update(cells)
                continue
            if label and not started and "," not in label:
                continue
            if label:
                if index == 0:
                    if names and "," not in label and not names[-1].endswith(")") and not cells:
                        names[-1] = f"{names[-1]} {label}"
                        continue
                    started = True
                    names.append(label)
                    if cells and not signed:
                        rows.append(cells)
                    continue
            if not label and cells and not signed and started and not finished:
                rows.append(cells)
        if index == 0:
            first = rows
        else:
            valid = book.get("valid")
            ends = [i for i, cells in enumerate(rows) if cells and set(cells.values()) == {valid}]
            if not ends or ends[-1] < 1:
                raise ValueError(f"{book['constituency']}: no total row on page {book['pages'][index]}")
            end = ends[-1]
            total.update(rows[end])
            nt.update(rows[end - 1])
            continuation = rows[: end - 1]
            if len(continuation) != len(first):
                raise ValueError(
                    f"{book['constituency']}: {len(continuation)} rows for {len(first)} candidates on page {book['pages'][index]}"
                )
            for position, cells in enumerate(continuation):
                first[position].update(cells)
        series = first
    return {**book, "names": names, "series": series, "nt": nt, "total": total}


ABBREVIATIONS = {
    "FF": "Fianna Fáil",
    "FG": "Fine Gael",
    "LAB": "The Labour Party",
    "SF": "Sinn Féin",
    "GP": "Green Party",
    "PBPA": "People Before Profit Alliance",
    "SP": "Socialist Party",
    "SWP": "Socialist Workers Party",
    "WP": "The Workers' Party",
    "CSP": "Comhar Críostaí – The Christian Solidarity Party",
    "SKIA": "South Kerry Independent Alliance",
    "SKI": "South Kerry Independent Alliance",
    "NONP": "Non-Party",
}


def fold(text: str) -> str:
    import unicodedata

    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def finish(book: dict[str, object], year: int, abbreviations: dict[str, str]) -> dict[str, object]:
    from ea.sources.ie.dail import tidy

    seats, quota, valid = int(book["seats"]), int(book["quota"]), int(book["valid"])
    label = book["constituency"]
    names, series, nt, total = book["names"], book["series"], book["nt"], book["total"]
    rounds = max(nt)
    if sorted(nt) != list(range(1, rounds + 1)) or sorted(total) != list(range(1, rounds + 1)):
        raise ValueError(f"{label}: printed rows do not cover counts 1 to {rounds}")
    if quota != valid // (seats + 1) + 1:
        raise ValueError(f"{label}: quota {quota} is not the Droop quota of {valid} for {seats} seats")
    if len(names) != len(series):
        raise ValueError(f"{label}: {len(names)} names for {len(series)} rows")
    candidates = []
    for index, (text, row) in enumerate(zip(names, series, strict=True)):
        match = re.match(r"^(?P<star>\*\s*)?(?P<surname>[^,]+),\s*(?P<fore>.+?)\s*(?:\((?P<party>[^()]+)\))?\s*$", text)
        if match is None:
            raise ValueError(f"{label}: cannot read the candidate {text!r}")
        party = (match["party"] or "").strip()
        code = re.sub(r"[^A-Z]", "", party.upper())
        if party and code not in abbreviations:
            raise ValueError(f"{label}: unknown party abbreviation {party!r}")
        present = sorted(row)
        if present == list(range(1, len(present) + 1)):
            row.update(dict.fromkeys(range(len(present) + 1, rounds + 1), 0))
        if sorted(row) != list(range(1, rounds + 1)):
            raise ValueError(f"{label}: {text} has counts {sorted(row)}")
        candidates.append(
            {
                "candidate": str(index),
                "name": f"{tidy(match['fore'])} {tidy(match['surname'])}",
                "ballot": f"{match['surname'].strip()}, {match['fore'].strip()}",
                "party": abbreviations[code] if party else "Non-Party",
                "incumbent": bool(match["star"]),
                "capitals": match["surname"].strip().isupper() and len(match["surname"].strip()) > 1,
                "series": row,
            }
        )
    for count in range(1, rounds + 1):
        if sum(c["series"][count] for c in candidates) + nt[count] != valid or total[count] != valid:
            raise ValueError(f"{label}: count {count} does not sum to the valid poll {valid}")
    if nt[1] != 0 or any(b < a for a, b in zip(list(nt.values()), list(nt.values())[1:])):
        raise ValueError(f"{label}: non-transferable papers are not cumulative")
    reached = {c["candidate"] for c in candidates if max(c["series"].values()) >= quota}
    last = {c["candidate"]: c["series"][rounds] for c in candidates}
    continuing = sorted((k for k, v in last.items() if v > 0 and k not in reached), key=lambda k: -last[k])
    elected = reached | set(continuing[: seats - len(reached)])
    capitals = {c["candidate"] for c in candidates if c["capitals"]}
    if len(elected) != seats or (capitals and capitals != elected):
        raise ValueError(f"{label}: elected {sorted(elected)} against {seats} seats and capitals {sorted(capitals)}")
    return {**book, "candidates": candidates, "elected": elected, "rounds": rounds}


def rows(pdf: str, year: int) -> dict[str, list[dict[str, object]]]:
    statutory: dict[str, tuple[str, int]] = {}
    for words in read_pages(pdf):
        for _, line in read_lines(words):
            name = " ".join(w[4] for w in line if not NUMBER.match(w[4]) and w[0] < min((x[0] for x in line if NUMBER.match(x[4])), default=0))
            figures = [w[4] for w in line if NUMBER.match(w[4])]
            if name and len(figures) >= 8 and len(figures[0]) == 1 and number(figures[1]) > 10000:
                statutory[fold(name)] = (name, int(figures[0]))
    units: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    counts: list[dict[str, object]] = []
    printed: list[dict[str, object]] = []
    for book in read_book(pdf):
        done = finish(assemble(book), year, ABBREVIATIONS)
        label, total_seats = statutory[fold(str(done["constituency"]))]
        valid, invalid = int(done["valid"]), int(done["invalid"])
        units.append(
            {
                "year": year,
                "constituency": label,
                "irish": None,
                "seats": int(done["seats"]),
                "total_seats": total_seats,
                "electorate": int(done["electorate"]),
                "poll": valid + invalid,
                "valid": valid,
                "invalid": invalid,
                "quota": int(done["quota"]),
                "rounds": int(done["rounds"]),
            }
        )
        for candidate in done["candidates"]:
            candidates.append(
                {
                    "year": year,
                    "constituency": label,
                    "candidate": candidate["candidate"],
                    "name": candidate["name"],
                    "ballot": candidate["ballot"],
                    "party": candidate["party"],
                    "gender": None,
                    "elected": candidate["candidate"] in done["elected"],
                }
            )
            for count, total in candidate["series"].items():
                counts.append({"year": year, "constituency": label, "candidate": candidate["candidate"], "count": count, "total": total})
        for count, value in done["nt"].items():
            printed.append({"year": year, "constituency": label, "count": count, "value": value})
    return {"units": units, "candidates": candidates, "counts": counts, "printed": printed}
