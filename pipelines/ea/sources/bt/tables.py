"""Readers for the Election Commission of Bhutan's result PDFs.

Each reader returns plain rows in the order the document prints them. Constituencies come back in print order as
well, so a caller can line one document up with another."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from ea.sources.bt.pdfwords import Word, stream

PARTIES = {
    "druknyamruptshogpa": "DNT",
    "drukphuensumtshogpa": "DPT",
    "drukchirwangtshogpa": "DCT",
    "peoplesdemocraticparty": "PDP",
    "bhutankuennyamparty": "BKP",
    "bhutantendrelparty": "BTP",
    "drukthuendreltshogpa": "DTT",
    "dnt": "DNT",
    "dpt": "DPT",
    "pdp": "PDP",
    "dct": "DCT",
    "bkp": "BKP",
    "btp": "BTP",
    "dtt": "DTT",
}
DZONGKHAGS = (
    "Bumthang",
    "Chhukha",
    "Dagana",
    "Gasa",
    "Haa",
    "Lhuentse",
    "Monggar",
    "Paro",
    "Pema Gatshel",
    "Punakha",
    "Samdrup Jongkhar",
    "Samtse",
    "Sarpang",
    "Thimphu",
    "Trashigang",
    "Trashi Yangtse",
    "Trongsa",
    "Tsirang",
    "Wangdue Phodrang",
    "Zhemgang",
)
HEADER = {
    "Sl.",
    "No.",
    "Demkhong",
    "Name",
    "of",
    "Political",
    "Party",
    "Sl",
    "Votes",
    "as",
    "CID/VPIC",
    "Total",
    "Registered",
    "Voter",
    "Received",
    "Male",
    "Female",
    "per",
    "EVM",
    "Postal",
    "Ballot",
}
ALIASES = {
    "lhuntse": "lhuentse",
    "mongar": "monggar",
    "zhamgang": "zhemgang",
    "chukha": "chhukha",
    "samdrupjhongkhar": "samdrupjongkhar",
    "wangdue": "wangduephodrang",
    "pematshel": "pemagatshel",
}


@dataclass
class Row:
    seat: int
    name: str
    party: str
    candidate: str
    evm: int | None
    postal: int | None
    total: int
    page: int
    elected: bool | None = None


@dataclass
class Seat:
    name: str
    male: int | None = None
    female: int | None = None
    registered: int | None = None
    rows: list[Row] = field(default_factory=list)
    stations: int | None = None
    evm_voters: int | None = None
    postal_voters: int | None = None
    no: tuple[int | None, int | None, int] | None = None
    page: int = 0


def squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("’", "").replace("'", ""))


def party_code(text: str) -> str:
    try:
        return PARTIES[squash(text)]
    except KeyError:
        raise ValueError(f"unknown party {text!r}") from None


def dzongkhag_index(text: str) -> int:
    key = squash(text)
    key = ALIASES.get(key, key)
    for index, name in enumerate(DZONGKHAGS):
        if squash(name) == key:
            return index
    raise ValueError(f"unknown dzongkhag {text!r}")


def _gy(word: Word) -> float:
    return word.page * 1000 + word.y


def _join(words: list[Word]) -> str:
    return " ".join(w.text for w in sorted(words, key=lambda w: (round(_gy(w) / 3), w.x)))


def _attach(rows: list[tuple[float, bool]], y: float) -> int:
    """Pick the row a wrapped line of text belongs to: the nearest row, preferring one that has no text of its own on
    its line."""
    bare = [i for i, (ry, own) in enumerate(rows) if not own and abs(ry - y) <= 24]
    pool = bare or list(range(len(rows)))
    return min(pool, key=lambda i: abs(rows[i][0] - y))


def _span(low: float, high: float, y: float) -> float:
    return 0.0 if low <= y <= high else min(abs(y - low), abs(y - high))


def na2008(path: Path) -> tuple[list[Seat], dict[str, int]]:
    """The 2008 constituency table: two candidates for each constituency, with postal, EVM, and total votes."""
    candidates: list[tuple[float, Row, bool]] = []
    spare: list[Word] = []
    totals: dict[str, int] = {}
    started = False
    for line in stream(path):
        text = " ".join(w.text for w in line)
        if text.startswith("Dzongkhag Constituency"):
            started = True
            continue
        if not started:
            continue
        found = re.search(r"Total Registered Voters = ([\d,]+)", text)
        if found:
            totals["registered"] = int(found[1].replace(",", ""))
        if line[0].text == "Total" and line[0].x > 200:
            digits = [w.number for w in line if w.number is not None]
            if len(digits) == 3:
                totals.update(postal=digits[0] or 0, evm=digits[1] or 0, total=digits[2] or 0)
            started = False
            continue
        digits = [w for w in line if w.number is not None and w.x >= 420]
        party = next((w for w in line if w.text in ("DPT", "PDP") and 380 < w.x < 420), None)
        own = any(255 <= w.x < 380 for w in line)
        if party is not None and len(digits) == 3:
            row = Row(0, "", party.text, "", digits[1].number, digits[0].number, digits[2].number or 0, line[0].page)
            candidates.append((_gy(line[0]), row, own))
        spare += [w for w in line if w.x >= 100 and w.number is None and w.text not in ("DPT", "PDP")]
    ys = [(y, own) for y, _, own in candidates]
    owned: dict[int, list[Word]] = {i: [] for i in range(len(candidates))}
    names: dict[int, list[Word]] = {k: [] for k in range(len(candidates) // 2)}
    for word in spare:
        if 255 <= word.x < 380:
            same = [i for i, (y, _) in enumerate(ys) if abs(y - _gy(word)) <= 3.5]
            owned[same[0] if same else _attach(ys, _gy(word))].append(word)
        elif 100 <= word.x < 250:
            seat = min(range(len(names)), key=lambda k: _span(ys[2 * k][0], ys[2 * k + 1][0], _gy(word)))
            names[seat].append(word)
    seats: list[Seat] = []
    for k in range(len(names)):
        rows = []
        for i in (2 * k, 2 * k + 1):
            row = candidates[i][1]
            row.candidate = _join(owned[i])
            row.seat = k + 1
            rows.append(row)
        seats.append(Seat(name=_join(names[k]).replace("- ", "-"), rows=rows))
    return seats, totals


def na_round(path: Path, *, kind: str) -> tuple[list[Seat], dict[str, int]]:
    """A party-level table (the 2013 and 2018 primary rounds) or a candidate-level one (the 2013 general election)
    with each constituency's registered voters printed beside its rows."""
    reg_low, reg_high = (280, 420) if kind == "primary13" else (300, 425)
    party_max = 180 if kind == "primary13" else 200
    candidates: list[tuple[float, Row, bool]] = []
    registered: list[tuple[float, int, int, int]] = []
    names: list[Word] = []
    spare: list[Word] = []
    own_words: list[list[Word]] = []
    totals: dict[str, int] = {}
    blocks: list[list[Row]] = []
    skipping = False
    for line in stream(path):
        text = " ".join(w.text for w in line)
        if text.startswith("Total Votes Secured"):
            skipping = True
            continue
        if re.fullmatch(r"\d+\.", line[0].text) and line[0].x < 45 and len(line) > 1:
            skipping = False
            continue
        if text.startswith("Grand Total") or (line[0].text == "Total" and " 47 " in f" {text} "):
            digits = [w.number or 0 for w in line if w.number is not None and w.x > 250]
            keys = ("registered_male", "registered_female", "registered", "evm", "postal", "total")
            if len(digits) >= 6:
                totals = dict(zip(keys, digits[-6:], strict=False))
            continue
        if skipping:
            continue
        reg = [w for w in line if w.number is not None and reg_low <= w.x < reg_high]
        res = [w for w in line if w.number is not None and w.x >= 420]
        lead = line[0] if line[0].number is not None and line[0].x < 70 else None
        cid = next((w for w in line if re.fullmatch(r"\d{11}", w.text)), None)
        if len(res) == 3 and (cid is not None if kind == "general13" else lead is not None):
            if kind == "general13":
                party = next(w for w in line if w.text in ("DPT", "PDP"))
                person = [w for w in line if 100 <= w.x < 195 and w.number is None]
                row = Row(0, "", party.text, "", res[0].number, res[1].number, res[2].number or 0, 0)
                candidates.append((_gy(line[0]), row, bool(person)))
                own_words.append(person)
            else:
                party = " ".join(
                    w.text for w in line if lead is not None and lead.x < w.x < party_max and w.number is None
                )
                row = Row(0, "", party, "", res[0].number, res[1].number, res[2].number or 0, line[0].page)
                if lead is not None and lead.number == 1:
                    blocks.append([])
                blocks[-1].append(row)
        elif kind == "general13" and len(res) == 0 and candidates:
            spare += [w for w in line if 100 <= w.x < 195 and w.number is None and w.text not in HEADER]
        if len(reg) == 3:
            triple = (reg[0].number or 0, reg[1].number or 0, reg[2].number or 0)
            repeated = bool(registered) and registered[-1][1:] == triple
            if not repeated:
                registered.append((_gy(line[0]), *triple))
        if kind == "general13":
            names += [
                w
                for w in line
                if w.x < 110 and w.number is None and w.text not in HEADER and not re.fullmatch(r"\d+\.?", w.text)
            ]
        elif lead is None and not res:
            names += [w for w in line if 150 < w.x < 300 and w.number is None]
    if kind == "general13":
        ys = [(y, own) for y, _, own in candidates]
        for word in spare:
            own_words[_attach(ys, _gy(word))].append(word)
        for (_, row, _), words in zip(candidates, own_words, strict=True):
            row.candidate = _join(words)
        blocks = [[row for _, row, _ in candidates[i : i + 2]] for i in range(0, len(candidates), 2)]
    seats: list[Seat] = []
    owned: list[list[Word]] = [[] for _ in registered]
    for word in names:
        owned[min(range(len(registered)), key=lambda k: abs(registered[k][0] - _gy(word)))].append(word)
    for index, block in enumerate(blocks):
        _, male, female, total = registered[index]
        for row in block:
            row.seat = index + 1
        label = _join(owned[index]).replace("_ ", "_").replace("- ", "-")
        seats.append(Seat(name=label, male=male, female=female, registered=total, rows=block))
    return seats, totals


def na2018(path: Path) -> tuple[list[tuple[str, list[Seat]]], dict[str, int]]:
    """The 2018 general election table, with a dzongkhag heading above each run of constituencies."""
    groups: list[tuple[str, list[Seat]]] = []
    current: Seat | None = None
    for line in stream(path):
        text = " ".join(w.text for w in line)
        if text.endswith(" Dzongkhag") and line[0].x < 70:
            groups.append((text.removesuffix(" Dzongkhag"), []))
        elif (
            groups
            and not any(w.number is not None for w in line)
            and ((text.endswith(" Demkhong") and line[0].x > 200) or (line[0].x >= 290 and text != "TOTAL"))
        ):
            current = Seat(name=text.removesuffix(" Demkhong"))
            groups[-1][1].append(current)
        elif current is not None and line[0].text != "TOTAL":
            digits = [w for w in line if w.number is not None]
            lead = line[0] if line[0].number is not None and line[0].x < 80 else None
            if lead is not None and len(digits) >= 4:
                tail = [w.number or 0 for w in digits[-3:]]
                middle = [w for w in line if 80 < w.x < 520 and w.number is None]
                party = " ".join(w.text for w in middle if w.x < 225)
                person = " ".join(w.text for w in middle if w.x >= 225)
                current.rows.append(
                    Row(len(current.rows) + 1, "", party, person, tail[0], tail[1], tail[2], line[0].page)
                )
            elif len(digits) == 3 and lead is None and current.registered is None and digits[0].x < 450:
                current.male, current.female, current.registered = (w.number for w in digits)
    return groups, {}


def na2023(path: Path) -> list[Seat]:
    """The 2023-24 primary round: for each constituency, its polling stations and voters, then each party's EVM,
    postal, and total votes."""
    seats: list[Seat] = []
    current: Seat | None = None
    party = ""
    evm = postal = 0
    elected = False
    for line in stream(path):
        left = [w for w in line if 30 <= w.x < 200]
        text = " ".join(w.text for w in left)
        if (
            left
            and left[0].x < 50
            and not left[0].text.startswith(("No.", "Total", "Annexure"))
            and all(w.number is None for w in left)
            and left[0].text not in ("Sl#", "DEMKHONG")
        ):
            current = Seat(name=" ".join(w.text for w in left), page=line[0].page)
            seats.append(current)
        for pattern, attr in (
            (r"Polling Stations: (\d+)", "stations"),
            (r"Total EVM Voters: (\d+)", "evm_voters"),
            (r"Total Postal Voters: (\d+)", "postal_voters"),
            (r"Total Registered Voters: (\d+)", "registered"),
        ):
            found = re.search(pattern, text)
            if found and current is not None:
                setattr(current, attr, int(found[1]))
        for word in line:
            if re.fullmatch(r"\([A-Z]{3}\)", word.text):
                party = word.text[1:-1]
            if word.text == "ELECTED":
                elected = True
        words = [w.text for w in line]
        numbers = [w.number for w in line if w.number is not None and w.x > 380]
        if "EVM:" in words and numbers:
            evm = numbers[0]
        elif "Ballot:" in words and numbers:
            postal = numbers[0]
        elif "Total:" in words and numbers and current is not None:
            current.rows.append(
                Row(len(current.rows) + 1, "", party, "", evm, postal, numbers[0], line[0].page, elected or None)
            )
            elected = False
    return seats


def nc2013(path: Path) -> list[Seat]:
    """The 2013 National Council table: a heading for each dzongkhag, then its candidates with votes and remarks."""
    seats: list[Seat] = []
    rows: list[tuple[float, Row, bool]] = []
    owner: dict[int, int] = {}
    spare: list[Word] = []
    own_words: list[list[Word]] = []
    yes: dict[str, int | bool] = {}
    for line in stream(path):
        if re.fullmatch(r"\d+\.", line[0].text) and line[0].x < 35 and len(line) > 1:
            seats.append(Seat(name=" ".join(w.text for w in line[1:]), page=line[0].page))
            yes = {}
            continue
        if not seats:
            continue
        seat = seats[-1]
        cid = next((w for w in line if re.fullmatch(r"\d{11}", w.text)), None)
        reg = [w for w in line if w.number is not None and 390 <= w.x < 520]
        res = [w for w in line if w.number is not None and w.x >= 540]
        remark = " ".join(w.text for w in line if w.x >= 690)
        words = [w.text for w in line]
        if len(reg) == 3:
            seat.male, seat.female, seat.registered = (w.number for w in reg)
        if "(NO)" in words and len(res) == 3:
            seat.no = (res[0].number, res[1].number, res[2].number or 0)
            continue
        if cid is None:
            if "(YES)" in words:
                continue
            if len(res) == 1 and res[0].x > 640:
                yes["total"] = res[0].number or 0
            elif len(res) == 2 and remark:
                yes["evm"], yes["postal"] = res[0].number or 0, res[1].number or 0
                yes["elected"] = remark == "Elected"
            else:
                spare += [w for w in line if 58 <= w.x < 180 and w.number is None and w.text not in HEADER]
            continue
        name = [w for w in line if 58 <= w.x < 180 and w.number is None]
        gewog = " ".join(w.text for w in line if 295 <= w.x < 390 and w.number is None)
        if len(res) == 3:
            row = Row(0, gewog, "", "", res[0].number, res[1].number, res[2].number or 0, line[0].page)
            row.elected = remark == "Elected"
        else:
            row = Row(0, gewog, "", "", int(yes["evm"]), int(yes["postal"]), int(yes["total"]), line[0].page)
            row.elected = bool(yes["elected"])
        rows.append((_gy(line[0]), row, bool(name)))
        own_words.append(name)
        owner[len(rows) - 1] = len(seats) - 1
    ys = [(y, own) for y, _, own in rows]
    for word in spare:
        own_words[_attach(ys, _gy(word))].append(word)
    for (_, row, _), words in zip(rows, own_words, strict=True):
        row.candidate = _join(words)
    for index, (_, row, _) in enumerate(rows):
        seats[owner[index]].rows.append(row)
    for seat in seats:
        for position, row in enumerate(seat.rows, 1):
            row.seat = position
    return seats


def nc2023(path: Path) -> list[Seat]:
    """The 2023 National Council table: for each dzongkhag, its polling stations and voters, then each candidate's EVM,
    postal, and total votes."""
    seats: list[Seat] = []
    held: Row | None = None
    capture = False
    heading: list[str] = []
    evm = postal = 0
    for line in stream(path):
        text = " ".join(w.text for w in line if w.x < 250)
        first = line[0]
        if first.x < 50 and re.fullmatch(r"[A-Z]{2,}", first.text):
            heading = [w.text for w in line if w.x < 250]
            seats.append(Seat(name=" ".join(heading).title(), page=first.page))
        for pattern, attr in (
            (r"Polling Stations: (\d+)", "stations"),
            (r"Total EVM Voters: (\d+)", "evm_voters"),
            (r"Total Postal Voters: (\d+)", "postal_voters"),
            (r"Total Registered Voters: (\d+)", "registered"),
        ):
            found = re.search(pattern, text)
            if found:
                setattr(seats[-1], attr, int(found[1]))
        for word in line:
            if re.fullmatch(r"\d{11}", word.text) and word.x >= 250:
                held = Row(len(seats[-1].rows) + 1, "", "", "", None, None, 0, word.page)
                capture = True
                continue
            if capture and held is not None and 250 <= word.x < 345 and word.number is None:
                if word.text == "ELECTED":
                    held.elected = True
                else:
                    held.candidate = f"{held.candidate} {word.text}".strip()
        words = [w.text for w in line]
        numbers = [w.number for w in line if w.number is not None and w.x > 380]
        if "EVM:" in words and numbers:
            capture = False
            evm = numbers[0]
        elif "Ballot:" in words and numbers:
            postal = numbers[0]
        elif "Total:" in words and numbers and held is not None:
            held.evm, held.postal, held.total = evm, postal, numbers[0]
            if "ELECTED" in words:
                held.elected = True
            seats[-1].rows.append(held)
            held = None
    return seats


def nc2007(path: Path) -> tuple[list[Seat], dict[str, int]]:
    """The 2007-08 National Council table, with each dzongkhag's candidates grouped by the votes cast it prints."""
    votes: list[tuple[float, int, Word | None]] = []
    labels: list[Word] = []
    casts: list[tuple[float, int, int]] = []
    names: list[Word] = []
    gewogs: list[Word] = []
    totals: dict[str, int] = {}
    started = False
    nos: dict[int, tuple[float, int]] = {}
    for line in stream(path):
        text = " ".join(w.text for w in line)
        if "Cast" in text and "Voters" in text:
            started = True
            continue
        if line[0].text in ("Page", "Sl.") or not started:
            continue
        if line[0].text == "Total" and line[0].x > 150:
            digits = [w.number or 0 for w in line if w.number is not None]
            if len(digits) == 3:
                totals = {"secured": digits[0], "cast": digits[1], "registered": digits[2]}
            continue
        yn = [w for w in line if w.text in ("YES", "NO", "No")]
        counted = [w for w in line if w.number is not None and 380 <= w.x < 420]
        for word in yn:
            number = next(w for w in line if w.number is not None and 380 <= w.x < word.x)
            if word.text == "YES":
                votes.append((_gy(word), number.number or 0, None))
            else:
                nos[len(votes) - 1] = (_gy(word), number.number or 0)
        if not yn:
            for word in counted:
                votes.append((_gy(word), word.number or 0, None))
        cast = [w for w in line if w.number is not None and w.x >= 455]
        if len(cast) == 2:
            casts.append((_gy(line[0]), cast[0].number or 0, cast[1].number or 0))
        labels += [
            w
            for w in line
            if w.x < 175
            and w.number is None
            and not w.text.startswith("(")
            and w.text not in ("Gewogs)", "YES", "NO", "No")
        ]
        names += [w for w in line if 255 <= w.x < 390 and w.number is None]
        gewogs += [w for w in line if 175 <= w.x < 255 and w.number is None]
    ordered = sorted(labels, key=lambda w: (round(_gy(w) / 3), w.x))
    found: list[str] = []
    i = 0
    while i < len(ordered):
        two = f"{ordered[i].text} {ordered[i + 1].text}" if i + 1 < len(ordered) else ""
        try:
            dzongkhag_index(two)
            found.append(two)
            i += 2
            continue
        except ValueError:
            pass
        try:
            dzongkhag_index(ordered[i].text)
            found.append(ordered[i].text)
        except ValueError:
            pass
        i += 1
    ys = [y for y, _, _ in votes]
    owned: list[list[Word]] = [[] for _ in votes]
    spot: list[list[Word]] = [[] for _ in votes]
    for word in names:
        owned[min(range(len(ys)), key=lambda k: abs(ys[k] - _gy(word)))].append(word)
    for word in gewogs:
        spot[min(range(len(ys)), key=lambda k: abs(ys[k] - _gy(word)))].append(word)
    seats: list[Seat] = []
    cursor = 0
    for (_, cast, registered), label in zip(sorted(casts), found, strict=True):
        seat = Seat(name=label, registered=registered)
        total = 0
        while total < cast and cursor < len(votes):
            _, count, _ = votes[cursor]
            if cursor in nos:
                total += nos[cursor][1]
                seat.no = (None, None, nos[cursor][1])
            total += count
            seat.rows.append(
                Row(len(seat.rows) + 1, _join(spot[cursor]), "", _join(owned[cursor]), None, None, count, 1)
            )
            cursor += 1
        if total != cast:
            raise ValueError(f"{label}: candidates add up to {total}, not the {cast} votes cast")
        seats.append(seat)
    return seats, totals


GE2024 = Path(__file__).with_name("ecb_ge2024.txt")


def na2024(path: Path = GE2024) -> list[tuple[str, Seat]]:
    """The 2024 National Assembly general election: each constituency with its dzongkhag, read from the
    transcription of the Commission's scanned demkhong-wise pages. The pages are images with no text layer, so the
    numbers are typed in `ecb_ge2024.txt` and checked here against the sums each page prints: the registered voters,
    the voters who turned out, and the votes on the machines and by post. A constituency whose rows disagree with its
    own page raises."""
    found: list[tuple[str, Seat]] = []
    printed: list[tuple[int, int, int]] = []
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if fields[0] == "S":
            figures = [int(field) for field in fields[4:]]
            stations, evm_reg, postal_reg, registered, male, female, turnout, evm, postal = figures
            if evm_reg + postal_reg != registered or male + female != turnout:
                raise ValueError(f"{fields[2]}: the registered voters or the turnout do not add up")
            seat = Seat(
                name=fields[3].title(),
                male=male,
                female=female,
                registered=registered,
                stations=stations,
                evm_voters=evm_reg,
                postal_voters=postal_reg,
                page=1,
            )
            printed.append((evm, postal, turnout))
            found.append((fields[1], seat))
        else:
            seat = found[-1][1]
            _, name, party, evm, postal, total, mark = fields
            row = Row(len(seat.rows) + 1, "", party, name, int(evm), int(postal), int(total), 1, mark == "E")
            if row.evm is None or row.evm + int(postal) != int(total):
                raise ValueError(f"{seat.name}: {name}'s machine and postal votes do not add up")
            seat.rows.append(row)
    for (_, seat), page in zip(found, printed, strict=True):
        if (
            sum(r.evm or 0 for r in seat.rows),
            sum(r.postal or 0 for r in seat.rows),
            sum(r.total for r in seat.rows),
        ) != page:
            raise ValueError(f"{seat.name}: the candidates' votes do not add up to the page's totals")
        if sum(bool(r.elected) for r in seat.rows) != 1 or not max(seat.rows, key=lambda r: r.total).elected:
            raise ValueError(f"{seat.name}: the elected candidate is not the one with the most votes")
    return found
