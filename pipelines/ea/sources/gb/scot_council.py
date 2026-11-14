import html
import io
import json
import os
import re
import struct
import subprocess
import time
import unicodedata
import zipfile
import zlib
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import ons_local, scottish_parliament, umit
from ea.store import DATA, connect

SOURCE = "gb.scot_council"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2022, 5, 5)
EMB = (
    "https://www.emb.scot/downloads/file/980/local-government-elections-2022-results-updated-with-postals-station-split"
)
MIRROR = "https://stv-results.prorep.org.uk"
INDEX = f"{MIRROR}/elections_2022_05.html"
BUNDLE = f"{MIRROR}/20220505_ME/ZIPs/All_Councils_All_wards.zip"


def bundle_wards() -> list[tuple[str, str, str]]:
    councils = {
        found.group(1)[:3]: found.group(1)
        for found in re.finditer(r'20220505_ME/pages/(C\d\d_[^/"]+)/', raw.cached(SOURCE, INDEX).path.read_text())
    }
    wards = []
    for info in zipfile.ZipFile(raw.cached(SOURCE, BUNDLE).path).infolist():
        found = re.match(r"Count_20220505_ME_(C\d\d)_W(\d\d)_MCEX_\d{8}_\d{6}__(.+)\.zip$", info.filename)
        if found is None:
            raise ValueError(f"unexpected bundle member {info.filename}")
        council, ward, tail = found.groups()
        name = f"Count_20220505_ME_{council}_W{ward}_MCEX_CandidateVotesPerStage__{tail}.pdf"
        wards.append((info.filename, name, f"{MIRROR}/20220505_ME/media/{councils[council]}/Ward_{ward}/{name}"))
    return wards


def stage_url(member: str, name: str, url: str) -> str:
    try:
        return raw.cached(SOURCE, f"{BUNDLE}#{member}/{name}").url
    except LookupError:
        return url


def fetch() -> None:
    if "SCOT_BY" in os.environ:
        return fetch_by()
    if "SCOT_YEAR" in os.environ:
        return fetch_hist()
    for url, suffix in ((EMB, ".xlsx"), (INDEX, ".html"), (BUNDLE, ".zip")):
        raw.fetch(SOURCE, url, suffix=suffix)
    path = raw.cached(SOURCE, BUNDLE).path
    content = path.read_bytes()
    retrieved = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    infos = {info.filename: info for info in zipfile.ZipFile(path).infolist()}
    for member, name, url in bundle_wards():
        info = infos[member]
        start = content.find(member.encode(), max(0, info.header_offset)) - 30
        method = struct.unpack("<H", content[start + 8 : start + 10])[0]
        name_length, extra_length = struct.unpack("<HH", content[start + 26 : start + 30])
        body = content[start + 30 + name_length + extra_length :][: info.compress_size]
        try:
            data = zlib.decompress(body, -15) if method == 8 else body
            good = content[start : start + 4] == b"PK\x03\x04" and zlib.crc32(data) == info.CRC
        except zlib.error:
            good = False
        if good and name in zipfile.ZipFile(io.BytesIO(data)).namelist():
            raw.store(
                SOURCE,
                f"{BUNDLE}#{member}/{name}",
                zipfile.ZipFile(io.BytesIO(data)).read(name),
                suffix=".pdf",
                retrieved_at=retrieved,
                media_type="application/pdf",
            )
            continue
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        try:
            raw.fetch(SOURCE, url, suffix=".pdf")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429 or error.response.status_code >= 500:
                raise
            print("missing", url, error.response.status_code)
        time.sleep(2)


SEATS = {"Ward 1 Barraigh agus Bhatarsaigh": 2, "Ward 6 Sgìr’ Ùige agus Carlabhagh": 2, "Ward 1 -North Isles": 3}
VACANT = {"Ward 1 -North Isles": "only two candidates were nominated, so one seat was left vacant"}
ARRANGEMENTS = "https://www.legislation.gov.uk/ssi/2021/369/schedule/made"
CHECKED: dict[str, str] = {
    "S12000005": "https://www.clacks.gov.uk/document/6700.pdf",
    "S12000036": "https://www.edinburgh.gov.uk/downloads/file/31146/candidate-votes-per-stage",
}
ALIASES = {"comhairle nan eilean siar": "na h eileanan siar", "comhairle eilean siar": "na h eileanan siar"}
NUMBER = r"-?[\d,]+(?:\.\d+)?"


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"['’`]", "", text.replace("&", " and "))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def council_key(text: str) -> str:
    text = fold(re.sub(r"^C\d\d_", "", text).replace("_", " "))
    text = re.sub(r"^the | council$", "", text).strip()
    return ALIASES.get(text, text)


def name_key(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def ward_key(text: str) -> str:
    return re.sub(r" ward$", "", re.sub(r"^ward \d+ ?", "", fold(text)))


def unwrap(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for line in lines:
        filled = [index for index in range(len(merged) - 1, -1, -1) if merged[index].strip()][:2]
        if (
            filled
            and line.strip()
            and not line[0].isspace()
            and re.search(NUMBER + r"\s*$", line.rstrip()) is None
            and merged[filled[0]][:1].isspace()
            and re.fullmatch(
                r"\s*(?:" + NUMBER + r"\s+)*" + NUMBER + r"(?:\s+(?:Elected|Excluded))?\s*", merged[filled[0]]
            )
        ):
            numbers = merged.pop(filled[0]).strip()
            fields = re.split(r"\s{2,}", line.strip())
            if fields[-1] in ("Elected", "Excluded"):
                numbers = f"{numbers}  {fields.pop()}"
            if (
                len(filled) > 1
                and merged[filled[1]][:1].isspace()
                and re.search(r"\d|Candidate|Affiliation", merged[filled[1]]) is None
            ):
                fields[1:] = [" ".join([merged.pop(filled[1]).strip(), *fields[1:]])]
            merged.append("  ".join([*fields, numbers]))
            continue
        merged.append(line)
    return merged


Stages = tuple[
    dict[str, Decimal],
    list[tuple[int, str, Decimal, str | None]],
    dict[int, Decimal],
    set[str],
    list[str],
    dict[str, str],
]


def read_stages(text: str) -> Stages:
    facts = {
        key: Decimal(found.group(1).replace(",", ""))
        for key, pattern in (
            ("valid", r"Total Number of Valid Votes\s+(" + NUMBER + ")"),
            ("seats", r"Positions to be Filled\s+(" + NUMBER + ")"),
            ("quota", r"Quota\s+(" + NUMBER + ")"),
        )
        if (found := re.search(pattern, text)) is not None
    }
    stage = 0
    rows: list[tuple[int, str, Decimal, str | None]] = []
    exhausted: dict[int, Decimal] = {}
    dropped: set[str] = set()
    odd = []
    affiliations: dict[str, str] = {}
    lines = unwrap(text.splitlines())
    for index, line in enumerate(lines):
        if (found := re.match(r"\s*Stage (\d+)\s*$", line)) is not None:
            stage = int(found.group(1))
            continue
        if (
            stage == 1
            and rows
            and index
            and lines[index - 1].strip()
            and re.fullmatch(r"\s{20,}\S.*", line)
            and not re.search(r"\d|Total|Votes", line)
        ):
            affiliations[rows[-1][1]] = f"{affiliations.get(rows[-1][1], '')} {line.strip()}".strip()
            continue
        if stage == 0 or not line.strip() or line[0].isspace():
            continue
        if (found := re.match(r"Exclusion of (.+?)(?:\s{2,}|$)", line)) is not None:
            dropped.add(name_key(found.group(1)))
            continue
        if re.match(r"(Surplus of|Report Name|Transfer of|Total\s)", line):
            continue
        status_found = re.search(r"\s(Elected|Excluded)$", line.rstrip())
        outcome = status_found.group(1) if status_found else None
        fields = re.split(r"\s{2,}", line.rstrip()[: status_found.start()].rstrip() if status_found else line.strip())
        numbers = []
        while fields and re.fullmatch(NUMBER, fields[-1]):
            numbers.insert(0, fields.pop())
        if not numbers and fields and rows and rows[-1][0] == stage:
            rows[-1] = (stage, f"{rows[-1][1]} {fields[0]}", rows[-1][2], rows[-1][3])
            continue
        if not numbers or not fields:
            odd.append(line.strip())
            continue
        value = Decimal(numbers[-1].replace(",", ""))
        if fields[0].startswith("Non-transferable votes"):
            exhausted[stage] = exhausted.get(stage, Decimal(0)) + value
            continue
        rows.append((stage, fields[0], value, outcome))
        if stage == 1 and len(fields) > 1:
            affiliations[fields[0]] = fields[1]
    return facts, rows, exhausted, dropped, odd, affiliations


def whole(token: str) -> Decimal:
    if token.strip() == "-":
        return Decimal(0)
    return Decimal(token.replace(",", "").replace(" ", "")).quantize(Decimal(1))


def declared_descriptions(declared: str) -> dict[str, str]:
    found = {}
    for line in declared.splitlines():
        fields = re.split(r"\s{2,}", line.strip())
        if len(fields) >= 3 and re.fullmatch(NUMBER, fields[2]) and re.fullmatch(r"[^\d,]+ [^\d,]+", fields[0]):
            found[fields[0].casefold()] = "" if fields[1] == "-" else fields[1]
    return found


def read_moray(text: str, declared: str) -> Stages:
    lines = text.splitlines()
    facts = {
        key: Decimal(found.group(1).replace(",", ""))
        for key, pattern in (
            ("cast", r"Total Votes Cast\s+(" + NUMBER + ")"),
            ("rejected", r"Rejected as Void\s+(" + NUMBER + ")"),
            ("quota", r"Quota\s+(" + NUMBER + ")"),
            ("electorate", r"Total Electorate\s+(" + NUMBER + ")"),
        )
        if (found := re.search(pattern, text)) is not None
    }
    if "cast" in facts and "rejected" in facts:
        facts["valid"] = facts["cast"] - facts["rejected"]
    head = next(index for index, line in enumerate(lines) if re.match(r"\s+Stage\s+Stage 1", line))
    last = len(re.findall(r"Stage \d+", lines[head]))
    header = next(line for line in lines[head + 1 :] if line.strip())
    excluding = [name for name in re.split(r"\s{2,}", header.replace("Exclusion of", "").strip()) if name]
    descriptions = declared_descriptions(declared)
    rows: list[tuple[int, str, Decimal, str | None]] = []
    exhausted: dict[int, Decimal] = {}
    odd = []
    affiliations: dict[str, str] = {}
    candidates: dict[str, list[int]] = {}
    started = False
    for line in lines[head + 1 :]:
        fields = re.split(r"\s{2,}", line.strip())
        if fields[0] == "Candidate":
            started = True
            continue
        if not started or not line.strip() or fields[0] in {"Rejected", "TOTAL"} or line.startswith("  " * 20):
            continue
        numbers = [int(field) for field in fields[1:] if re.fullmatch(r"-?\d+", field)]
        if fields[0] == "Non-transferable":
            count = len(numbers)
            totals = (
                numbers[1::2]
                if count == 2 * (last - 1)
                else numbers[:1] + numbers[2::2]
                if count == 2 * last - 3
                else numbers
                if count == last - 1
                else []
            )
            if len(totals) != last - 1:
                odd.append(line.strip())
            for stage, total in enumerate(totals, start=2):
                exhausted[stage] = Decimal(total)
            continue
        if len(fields) < 3 or not numbers:
            break
        candidates[fields[0]] = numbers
        affiliations[fields[0]] = descriptions.get(fields[0].casefold(), fields[1])
    dropped = {
        name_key(label) for label in candidates for name in excluding if label.casefold().endswith(name.casefold())
    }
    leader = ""
    for stage in range(1, last + 1):
        stage_rows = []
        for label, numbers in candidates.items():
            out = name_key(label) in dropped and (
                stage >= 2 + next(i for i, name in enumerate(excluding) if label.casefold().endswith(name.casefold()))
            )
            if out:
                votes = Decimal(0)
            elif stage == 1:
                votes = Decimal(numbers[0])
            elif 2 * stage - 2 < len(numbers):
                votes = Decimal(numbers[2 * stage - 2])
            else:
                odd.append(label)
                votes = Decimal(0)
            stage_rows.append((label, votes))
        leader = max(stage_rows, key=lambda row: row[1])[0]
        rows.extend(
            (stage, label, votes, "Elected" if stage == last and label == leader else None)
            for label, votes in stage_rows
        )
    exhausted.setdefault(1, Decimal(0))
    return facts, rows, exhausted, dropped, odd, affiliations


def read_tables(text: str) -> Stages:
    rows: list[tuple[int, str, Decimal, str | None]] = []
    exhausted: dict[int, Decimal] = {}
    dropped: set[str] = set()
    affiliations: dict[str, str] = {}
    stage = 0
    after = False
    for line in text.splitlines():
        was, after = after, False
        if (found := re.match(r"Stage (\d+)\s*$", line)) is not None:
            stage = int(found.group(1))
        elif (found := re.match(r"Exclusion of (.+?)\s*$", line)) is not None:
            dropped.add(name_key(found.group(1)))
        elif stage and (
            found := re.match(r"\s+Non-transferable votes\s+" + NUMBER + r"\s+(" + NUMBER + r")\s*$", line)
        ):
            exhausted[stage] = whole(found.group(1))
        elif stage and (
            found := re.match(r"\s(\S.*?)\s{2,}(\S.*?)\s{2,}-?\s?" + NUMBER + r"\s+(" + NUMBER + r")\s*$", line)
        ):
            rows.append((stage, found.group(1), whole(found.group(3)), None))
            after = True
            affiliations.setdefault(found.group(1), found.group(2))
        elif was and (found := re.match(r"(\s+)(\S[^\d]*?)\s*$", line)) is not None:
            if len(found.group(1)) < 4:
                raw_name = rows[-1][1]
                rows = [(a, f"{raw_name} {found.group(2)}" if b == raw_name else b, c, d) for a, b, c, d in rows]
                affiliations[f"{raw_name} {found.group(2)}"] = affiliations.pop(raw_name)
            elif stage == 1:
                affiliations[rows[-1][1]] += f" {found.group(2)}"
            after = True
    names = {raw_name: " ".join(reversed(raw_name.split(", ", 1))) for _, raw_name, _, _ in rows}
    rows = [(a, names[b], c, d) for a, b, c, d in rows]
    affiliations = {names[key]: value for key, value in affiliations.items()}
    facts: dict[str, Decimal] = {}
    if rows:
        final = max(number for number, *_ in rows)
        leader = max((row for row in rows if row[0] == final), key=lambda row: row[2])[1]
        rows = [(a, b, c, "Elected" if a == final and b == leader else d) for a, b, c, d in rows]
        facts["valid"] = sum((votes for number, _, votes, _ in rows if number == 1), Decimal(0)) + exhausted.get(
            1, Decimal(0)
        )
    return facts, rows, exhausted, dropped, [], affiliations


def read_matrix(text: str) -> Stages:
    facts: dict[str, Decimal] = {}
    if (found := re.search(r"Total Votes: (\S+), Positions to be Filled: (\d+), Quota: (\S+)", text)) is not None:
        facts = {
            "valid": whole(found.group(1).rstrip(",")),
            "seats": Decimal(found.group(2)),
            "quota": whole(found.group(3)),
        }
    rows: list[tuple[int, str, Decimal, str | None]] = []
    exhausted: dict[int, Decimal] = {}
    dropped: set[str] = set()
    odd = []
    affiliations: dict[str, str] = {}
    for page in text.split("\f"):
        stages = sorted({int(number) for number in re.findall(r"Stage (\d+) - ", page)})
        if not stages:
            continue
        label = ""
        for line in page.splitlines():
            if not line.strip() or line.startswith(("Name", "Total", "Report Name", "Contest Name")):
                continue
            if line[0].isspace():
                found = re.fullmatch(r"\s{10,}(\S.*?)\s*", line)
                if label and found is not None and re.search(r"\d", found.group(1)) is None and stages[0] == 1:
                    affiliations[label] = f"{affiliations[label]} {found.group(1)}"
                continue
            status = re.search(r"\s(Elected|Excluded) Stage (\d+)", line)
            body = line[: status.start()] if status else line
            fields = re.split(r"\s{2,}", body.strip())
            tail = line[status.end() :] if status else line[len(body) :]
            leftover = fields[0] in {"No further preference", "Remainder not transferred"}
            numbers = re.findall(
                r"-?\d[\d,]*(?:\.\d+)?|(?<=\s)-(?=\s)", tail if status else " ".join(fields[1 if leftover else 2 :])
            )
            values = [whole(number) for number in numbers]
            totals = values[:1] + values[2::2] if stages[0] == 1 else values[1::2]
            if leftover:
                for stage, value in zip(stages, totals, strict=False):
                    exhausted[stage] = exhausted.get(stage, Decimal(0)) + value
                continue
            if len(fields) < 2:
                odd.append(line.strip())
                continue
            label = fields[0]
            if label not in affiliations:
                affiliations[label] = fields[1]
            if len(totals) != len(stages):
                odd.append(line.strip())
                continue
            for stage, votes in zip(stages, totals, strict=True):
                outcome = (
                    "Elected" if status and status.group(1) == "Elected" and int(status.group(2)) == stage else None
                )
                rows.append((stage, label, votes, outcome))
            if status and status.group(1) == "Excluded":
                dropped.add(name_key(label))
    return facts, rows, exhausted, dropped, odd, affiliations


def read_script(text: str, declared: str) -> Stages:
    facts: dict[str, Decimal] = {}
    if (
        found := re.search(r"There were ([\d,]+) valid votes cast and ([\d,]+) votes had to be rejected", text)
    ) is not None:
        facts["valid"] = whole(found.group(1))
        facts["rejected"] = whole(found.group(2))
    if (found := re.search(r"quota for the ward is ([\d,]+)", text)) is not None:
        facts["quota"] = whole(found.group(1))
    rows: list[tuple[int, str, Decimal, str | None]] = []
    exhausted: dict[int, Decimal] = {}
    dropped = {name_key(name) for name in re.findall(r"This is ([^.]+?)\.", " ".join(text.split()))}
    stage = 1
    inside = False
    for line in text.splitlines():
        if re.match(r"\s*At Stage 1 the number", line):
            inside = True
            continue
        if (found := re.search(r"give the Stage (\d+) result as:", line)) is not None:
            stage = int(found.group(1))
            inside = True
            continue
        if not inside:
            continue
        if (found := re.match(r"Value of non-transferable ballot papers - (" + NUMBER + ")", line)) is not None:
            exhausted[stage] = exhausted.get(stage - 1, Decimal(0)) + whole(found.group(1))
            inside = False
            continue
        if stage == 1:
            if (found := re.match(r"(\S.*?)\s{2,}(" + NUMBER + r")\s*$", line)) is not None:
                rows.append((1, found.group(1), whole(found.group(2)), None))
            elif line.startswith("At this stage"):
                inside = False
        elif (found := re.match(r"([A-Z].*?) - (" + NUMBER + r")\s*$", line)) is not None:
            rows.append((stage, found.group(1), whole(found.group(2)), None))
    exhausted.setdefault(1, Decimal(0))
    elected = re.search(r"basis of the foregoing (.+?) will be declared elected", " ".join(text.split()))
    if rows and elected is not None:
        final = max(number for number, *_ in rows)
        rows = [(a, b, c, "Elected" if a == final and b == elected.group(1) else d) for a, b, c, d in rows]
    descriptions = declared_descriptions(declared)
    affiliations = {
        label: descriptions[label.casefold()] for _, label, _, _ in rows if label.casefold() in descriptions
    }
    return facts, rows, exhausted, dropped, [], affiliations


def read_wide(text: str) -> Stages:
    facts = {
        key: Decimal(found.group(1).replace(",", ""))
        for key, pattern in (
            ("valid", r"Total Number of Valid Votes\s+(" + NUMBER + ")"),
            ("seats", r"Number of Vacancies\s+(" + NUMBER + ")"),
            ("quota", r"Quota\s+(" + NUMBER + ")"),
        )
        if (found := re.search(pattern, text)) is not None
    }
    cells: dict[str, list[Decimal]] = {}
    elected: set[str] = set()
    affiliations: dict[str, str] = {}
    odd = []
    label = ""
    for line in text.splitlines():
        if not line.strip() or re.match(r"(Name of|Total|Number of|Quota|LIVE|Election)", line):
            continue
        if line[0].isspace():
            if label and re.fullmatch(r"\s+(?:-?[\d,]+(?:\.\d+)?\s*)+", line):
                cells[label].extend(Decimal(token.replace(",", "")) for token in line.split())
            continue
        if line.rstrip().endswith(" Ward"):
            continue
        status = re.search(r"\s(Elected)\s*$", line)
        fields = re.split(r"\s{2,}", (line[: status.start()] if status else line).strip())
        label = fields[0]
        body = fields[1:]
        if not label.startswith("Non-transferable") and body and not re.match(r"-?\d", body[0]):
            affiliations.setdefault(label, body.pop(0))
        tokens = re.findall(r"-?\d[\d,]*(?:\.\d+)?", " ".join(body))
        cells.setdefault(label, []).extend(Decimal(token.replace(",", "")) for token in tokens)
        if status:
            elected.add(label)
    rows: list[tuple[int, str, Decimal, str | None]] = []
    exhausted: dict[int, Decimal] = {}
    dropped: set[str] = set()
    for label, values in cells.items():
        totals = values[:1] + values[2::2]
        if label.startswith("Non-transferable"):
            exhausted = {stage: total for stage, total in enumerate(totals, 1)}
            continue
        for stage, total in enumerate(totals, 1):
            rows.append((stage, label, total, "Elected" if label in elected and stage == len(totals) else None))
        if label not in elected and totals and totals[-1] == 0:
            dropped.add(name_key(label))
    return facts, rows, exhausted, dropped, odd, affiliations


def read_estv(path: Path, text: str) -> Stages:
    facts = {
        key: Decimal(found.group(1).replace(",", ""))
        for key, pattern in (
            ("valid", r"Valid Votes:?\s+(" + NUMBER + ")"),
            ("seats", r"Number to be elected:?\s+(" + NUMBER + ")"),
            ("quota", r"Quota:?\s+(" + NUMBER + ")"),
        )
        if (found := re.search(pattern, text, re.IGNORECASE)) is not None
    }
    if "quota" in facts and facts["quota"] == facts["quota"].to_integral_value():
        facts["quota"] = facts["quota"].quantize(Decimal(1))
    markup = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    figure = re.compile(NUMBER)
    Word = tuple[float, float, float, float, str]
    values: dict[str, dict[int, Decimal]] = {}
    won: set[str] = set()
    odd: list[str] = []
    layout: tuple[list[float], list[int], float | None] | None = None
    places: list[tuple[float, str]] = []

    def read_table(table: list[Word], bounds: list[float], numbers: list[int], edge: float | None) -> float | None:
        nonlocal places
        lead = [
            word for word in table if bounds[0] <= word[0] < bounds[1] and (figure.fullmatch(word[4]) or word[4] == "-")
        ]
        anchors: list[float] = []
        for middle in sorted((word[1] + word[3]) / 2 for word in lead):
            if not anchors or middle - anchors[-1] > 3:
                anchors.append(middle)
        if not anchors:
            return None
        if edge is None:
            edge = min(word[0] for word in lead)
        cells: dict[float, list[Word]] = {anchor: [] for anchor in anchors}
        for word in table:
            middle = (word[1] + word[3]) / 2
            if middle < anchors[0] - (6 if word[2] >= edge - 1 else 9):
                continue
            anchor = min(anchors, key=lambda value: abs(value - middle))
            if abs(anchor - middle) <= 14:
                cells[anchor].append(word)
        seen: list[tuple[float, str]] = []
        for anchor in anchors:
            label = " ".join(
                word[4]
                for word in sorted(cells[anchor], key=lambda word: (round(word[1]), word[0]))
                if word[2] < edge - 1 and word[4] != "-" and not figure.fullmatch(word[4])
            ).replace("- ", "-")
            if label.startswith("Non-"):
                label = "Non-transferable"
            elif label.endswith("Total"):
                label = "Total"
            if not label:
                label = next((name for at, name in places if abs(at - anchor) < 4), "")
            seen.append((anchor, label))
        places = seen
        counted = [number for number in numbers if number]
        sums = sorted(
            (word for anchor, label in seen if label == "Total" for word in cells[anchor] if figure.fullmatch(word[4])),
            key=lambda word: word[0],
        )
        if counted[0] == 1 and len(sums) == len(counted) + 1:
            counted.insert(1, counted[1] - 1)
        spans = [(word[0], word[2]) for word in sums] if len(sums) == len(counted) else []
        for anchor, label in seen:
            members = cells[anchor]
            if label == "Total":
                continue
            if not label:
                odd.append(f"unlabeled row at stage {numbers[0]}")
                continue
            if any(word[4] == "Elected" for word in members):
                won.add(label)
            held = values.setdefault(label, {})
            found: list[Word]
            if 0 in numbers:
                low = bounds[numbers.index(0)]
                if any(word[0] >= low and figure.fullmatch(word[4]) for word in members):
                    won.add(label)
            if spans:
                for stage, (low, high) in zip(counted, spans, strict=True):
                    found = [
                        word
                        for word in members
                        if figure.fullmatch(word[4]) and word[0] <= high + 2 and word[2] >= low - 2
                    ]
                    if len(found) > 1:
                        odd.append(f"{label} stage {stage}")
                    if found:
                        amount = Decimal(found[0][4].replace(",", ""))
                        if held.setdefault(stage, amount) != amount:
                            odd.append(f"{label} stage {stage}: {held[stage]} and {amount}")
                continue
            for stage, low, high in zip(numbers, bounds, bounds[1:], strict=False):
                if stage == 0:
                    continue
                inside = [word for word in members if low <= word[0] < high and word[2] >= edge - 1]
                found = sorted((word for word in inside if figure.fullmatch(word[4])), key=lambda word: word[2])
                if not found:
                    if any(word[4] not in ("-", "Elected") for word in inside):
                        odd.append(f"{label} stage {stage}")
                    continue
                amount = Decimal((found[0] if stage == 1 else found[-1])[4].replace(",", ""))
                if held.setdefault(stage, amount) != amount:
                    odd.append(f"{label} stage {stage}: {held[stage]} and {amount}")
        return edge

    for page in markup.split("<page ")[1:]:
        words: list[Word] = [
            (float(left), float(top), float(right), float(bottom), html.unescape(word))
            for left, top, right, bottom, word in re.findall(
                r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<', page
            )
        ]
        marks = sorted(word[1] for word in words if word[4] in ("©", "Preference", "Page", "Reason"))
        heads = sorted(
            (word[1], word[0], int(following[4]))
            for word, following in pairwise(words)
            if word[4] == "Stage" and following[4].isdigit() and abs(word[1] - following[1]) < 2
        )
        groups: list[list[tuple[float, float, int]]] = []
        for head in heads:
            if groups and head[0] - groups[-1][0][0] < 6:
                groups[-1].append(head)
            else:
                groups.append([head])
        if layout is not None:
            end = min([groups[0][0][0] - 20] if groups else [], default=float("inf"))
            end = min([end, *(mark for mark in marks if mark < end)])
            bounds, numbers, edge = layout
            read_table([word for word in words if word[1] < end], bounds, numbers, edge)
            if end < float("inf") or not groups and marks:
                layout = None
        for index, group in enumerate(groups):
            top = group[0][0] - 20
            end = groups[index + 1][0][0] - 20 if index + 1 < len(groups) else float("inf")
            closed = [mark for mark in marks if top < mark < end]
            end = min([end, *closed])
            columns = sorted((left, stage) for _, left, stage in group if stage > 1)
            stages = [stage for _, stage in columns]
            if stages != list(range(stages[0], stages[0] + len(stages))):
                odd.append(f"stage headers {stages}")
                layout = None
                continue
            header = [
                word
                for word in words
                if top <= word[1] < top + 60 and word[4] in ("Stage", "First", "Preferences", "Candidates")
            ]
            first = next((word for word in header if word[4] == "First"), None)
            bottom = max(word[3] for word in header)
            bounds = [first[0] - 40] if first else []
            chosen = [word[0] for word in words if word[4] == "Elected" and top <= word[1] < bottom]
            bounds += [*(left - 4 for left, _ in columns), *(chosen[0] - 4 for _ in chosen[:1]), float("inf")]
            numbers = ([1] if first else []) + stages + [0 for _ in chosen[:1]]
            edge = read_table([word for word in words if bottom < word[1] < end], bounds, numbers, None)
            layout = (bounds, numbers, edge) if end == float("inf") else None
    rows: list[tuple[int, str, Decimal, str | None]] = []
    exhausted: dict[int, Decimal] = {}
    dropped: set[str] = set()
    last = max((max(held) for held in values.values() if held), default=0)
    standing = {
        label for label, held in values.items() if not label.startswith("Non-") and held.get(last, Decimal(0)) > 0
    }
    reached = {
        label
        for label, held in values.items()
        if not label.startswith("Non-") and "quota" in facts and max(held.values(), default=0) >= facts["quota"]
    }
    if not won and len(reached | standing) == facts.get("seats"):
        won = reached | standing
    elif not won and len(reached) == facts.get("seats"):
        won = reached
    for label, held in values.items():
        stages = sorted(held)
        if not stages:
            continue
        if stages != list(range(1, len(stages) + 1)):
            odd.append(f"{label} stages {stages}")
            continue
        if label.lower().startswith("non-transferable"):
            exhausted = dict(held)
            continue
        for stage in stages:
            rows.append((stage, label, held[stage], "Elected" if label in won and stage == stages[-1] else None))
        if label not in won and held[stages[-1]] == 0:
            dropped.add(name_key(label))
    for stage in {number for number, _, _, _ in rows} - set(exhausted):
        exhausted[stage] = Decimal(0)
    return facts, rows, exhausted, dropped, odd, {}


def read_other(text: str, declared: str) -> Stages:
    if "Stage 1 - First Preference" in text:
        return read_matrix(text)
    if re.search(r"Candidate name\s+Party", text):
        return read_tables(text)
    if "First Pref." in text:
        return read_moray(text, declared)
    if "Results Script for Returning Officer" in text:
        return read_script(text, declared)
    return {}, [], {}, set(), [], {}


def parse() -> None:
    if "SCOT_BY" in os.environ:
        return parse_by()
    if "SCOT_YEAR" in os.environ:
        return parse_hist()
    emb = pl.read_excel(raw.cached(SOURCE, EMB).path, sheet_name="RawDetails", engine="calamine")
    features = [
        feature
        for key in ("wd22", "lad22")
        for url in ons_local.pages(key)
        for feature in json.loads(raw.cached(ons_local.SOURCE, url).path.read_text())["features"]
    ]
    wards = {
        (council_key(p["LAD22NM"]), ward_key(p["WD22NM"])): p
        for feature in features
        if (p := feature["properties"]).get("WD22CD", "").startswith("S13")
    }
    if len(wards) != 355:
        raise ValueError(f"{len(wards)} Scottish wards in the December 2022 layer")
    councils = {council_key(p["LAD22NM"]): p["LAD22CD"] for p in wards.values()}
    emb_wards: dict[str, list[tuple[str, int, bool, int | None, str, str]]] = {}
    emb_facts: dict[str, tuple[int, int, int, int]] = {}
    for row in emb.iter_rows(named=True):
        key = f"{row['Authority Name']}\x1f{row['Ward Number and Description']}"
        emb_wards.setdefault(key, []).append(
            (
                str(row["CandidateName"]),
                int(row["NumberOfFirstPreferenceVotes"] or 0),
                row["CandidateElected"] == "Yes",
                None if row["CandidateStageElected"] is None else int(row["CandidateStageElected"]),
                str(row["Affiliation"]),
                str(row["AffiliationNational"]),
            )
        )
        emb_facts[key] = (
            int(row["Electorate"]),
            int(row["Total Ballot Papers"] or 0),
            int(row["Total Ballot Papers Included In Count"] or 0),
            int(row["Total Ballot Papers Rejected"] or 0),
        )
    units: list[dict[str, str | int | bool | None]] = []
    totals: list[dict[str, str | int | bool | None]] = []
    matched: set[str] = set()
    unread: set[str] = set()
    for member, name, url in bundle_wards():
        locator = stage_url(member, name, url)
        council = council_key(re.findall(r"media/(C\d\d_[^/]+)/", url)[0])
        try:
            path = raw.cached(SOURCE, locator).path
        except LookupError:
            unread.add(council)
            units.append({"council_code": councils.get(council), "member": member, "status": "no stage report"})
            continue
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        facts, rows, exhausted, dropped, odd, _ = read_stages(text)
        names = [label for number, label, _, _ in rows if number == 1]
        found_emb = [
            key
            for key, lines in emb_wards.items()
            if council_key(key.split("\x1f")[0]) == council
            and {name_key(line[0]) for line in lines} == {name_key(label) for label in names}
        ]
        key = found_emb[0] if len(found_emb) == 1 else None
        first = {
            label: line
            for line in (emb_wards[key] if key else [])
            for label in names
            if name_key(label) == name_key(line[0])
        }
        description = key.split("\x1f")[1] if key else None
        ons = wards.get((council, ward_key(description))) if description else None
        elected = {label for _, label, _, outcome in rows if outcome == "Elected"}
        stages = sorted({number for number, _, _, _ in rows})
        status = "loaded"
        if odd:
            status = f"unread lines: {odd[:3]}"
        elif key is None:
            status = "no single EMB ward with the same candidates"
        elif ons is None:
            status = "no ONS ward code"
        elif any(Decimal(first[label][1]) != votes for number, label, votes, _ in rows if number == 1):
            status = "stage 1 differs from EMB first preferences"
        elif facts.get("valid") != Decimal(emb_facts[key][2]):
            status = "valid votes differ from EMB"
        elif elected != {label for label, line in first.items() if line[2]}:
            status = "elected differ from EMB"
        elif len(elected) != facts.get("seats"):
            status = "elected do not fill the seats"
        else:
            for number in stages:
                total = exhausted.get(number, Decimal(0))
                for at, _, votes, _ in rows:
                    if at == number:
                        total += votes
                if total != facts["valid"]:
                    status = f"stage {number} sums to {total}, not {facts['valid']}"
                    break
            for candidate in sorted(elected):
                if status != "loaded":
                    break
                best = max(votes for _, label, votes, _ in rows if label == candidate)
                when = min(number for number, label, _, outcome in rows if label == candidate and outcome == "Elected")
                if best < facts["quota"] and when != stages[-1]:
                    status = f"{candidate} elected below the quota before the last stage"
                elif first[candidate][3] != when:
                    status = f"{candidate} elected at stage {when}, EMB says {first[candidate][3]}"
        if key is not None:
            matched.add(key)
        electorate, ballots, _, invalid = emb_facts[key] if key else (None, None, None, None)
        units.append(
            {
                "council_code": councils.get(council),
                "member": member,
                "locator": locator,
                "status": status,
                "emb_ward": description,
                "ward_code": ons["WD22CD"] if ons else None,
                "ward_name": ons["WD22NM"] if ons else None,
                "seats": int(facts["seats"]) if "seats" in facts else None,
                "electorate": electorate,
                "ballots": ballots,
                "valid": str(facts.get("valid")),
                "invalid": invalid,
                "quota": str(facts.get("quota")),
                "uncontested": False,
            }
        )
        if status != "loaded" or ons is None:
            continue
        order = {label: number for number, label in enumerate(names, start=1)}
        for number, label, votes, _ in rows:
            line = first[label]
            totals.append(
                {
                    "ward_code": ons["WD22CD"],
                    "number": order[label],
                    "name": re.sub(
                        r"[ÂÃ][\x80-\xbf]",
                        lambda pair: pair.group(0).encode("latin-1").decode("utf-8"),
                        line[0],
                    )
                    if "Ã" in label
                    else label,
                    "party": line[5],
                    "elected": line[2],
                    "outcome": "elected" if line[2] else ("eliminated" if name_key(label) in dropped else "lost"),
                    "stage": number,
                    "votes": str(votes),
                }
            )
        for number in stages:
            totals.append(
                {
                    "ward_code": ons["WD22CD"],
                    "number": 0,
                    "name": None,
                    "party": None,
                    "elected": False,
                    "outcome": None,
                    "stage": number,
                    "votes": str(exhausted.get(number, Decimal(0))),
                }
            )
    for key, lines in emb_wards.items():
        authority, description = key.split("\x1f")
        council = council_key(authority)
        if key in matched or emb_facts[key][2] == 0 or council not in unread:
            continue
        ons = wards.get((council, ward_key(description)))
        electorate, ballots, included, invalid = emb_facts[key]
        units.append(
            {
                "council_code": councils.get(council),
                "member": None,
                "locator": None,
                "status": "first preferences do not sum to the included ballots"
                if sum(line[1] for line in lines) != included
                else ("loaded" if ons else "no ONS ward code"),
                "emb_ward": description,
                "ward_code": ons["WD22CD"] if ons else None,
                "ward_name": ons["WD22NM"] if ons else None,
                "seats": sum(line[2] for line in lines),
                "electorate": electorate,
                "ballots": ballots,
                "valid": str(included),
                "invalid": invalid,
                "quota": None,
                "uncontested": False,
                "emb_only": True,
            }
        )
        if ons and sum(line[1] for line in lines) == included:
            for number, line in enumerate(lines, start=1):
                totals.append(
                    {
                        "ward_code": ons["WD22CD"],
                        "number": number,
                        "name": line[0],
                        "party": line[5],
                        "elected": line[2],
                        "outcome": "elected" if line[2] else "lost",
                        "stage": 1,
                        "votes": str(line[1]),
                        "stage_elected": line[3],
                    }
                )
    for key, lines in emb_wards.items():
        if key in matched or not all(line[2] for line in lines) or emb_facts[key][2] != 0:
            continue
        authority, description = key.split("\x1f")
        council = council_key(authority)
        ons = wards.get((council, ward_key(description)))
        units.append(
            {
                "council_code": councils.get(council),
                "member": None,
                "locator": None,
                "status": "loaded" if ons else "no ONS ward code",
                "emb_ward": description,
                "ward_code": ons["WD22CD"] if ons else None,
                "ward_name": ons["WD22NM"] if ons else None,
                "seats": SEATS.get(description, len(lines)),
                "electorate": emb_facts[key][0],
                "ballots": None,
                "valid": None,
                "invalid": None,
                "quota": None,
                "uncontested": True,
            }
        )
        if ons:
            for number, line in enumerate(lines, start=1):
                totals.append(
                    {
                        "ward_code": ons["WD22CD"],
                        "number": number,
                        "name": line[0],
                        "party": line[5],
                        "elected": True,
                        "outcome": "elected",
                        "stage": 0,
                        "votes": None,
                    }
                )
    frame = pl.DataFrame(units, infer_schema_length=None)
    print(frame.group_by("status").len().sort("len", descending=True).rows())
    print("council names", {row["council_code"] for row in units if row["council_code"] is None})
    codes = set(frame.filter(pl.col("status") == "loaded")["ward_code"]) | set(frame["council_code"].drop_nulls())
    with connect() as conn:
        conn.execute("create temp table feature (code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for feature in features:
                code = feature["properties"].get("WD22CD") or feature["properties"].get("LAD22CD")
                if code in codes:
                    copy.write_row((code, json.dumps(feature["geometry"])))
        shapes = conn.execute(
            """select code, encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select code, st_multi(st_collectionextract(st_makevalid(st_union(
                       st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))))), 3)) as geom
                     from feature group by code) whole"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "units.parquet")
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(PARSED / "totals.parquet")
    pl.DataFrame(
        shapes, schema={"code": pl.String, "geom": pl.String, "label_point": pl.String}, orient="row"
    ).write_parquet(PARSED / "shapes.parquet")


VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED, QUOTA = 1, 2, 3, 4, 5, 21, 26
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"


def party_of(cs: Changeset, parties: dict[str, UUID], label: str | None) -> UUID | None:
    if label is None or label == "Independent" or label in umit.NOT_PARTIES:
        return None
    key = scottish_parliament.PARTY_KEYS.get(label.lower()) or umit.ALIASES.get(label) or slug(label)
    if key not in parties:
        if key in umit.EXISTING:
            parties[key] = cs.key(*umit.EXISTING[key])
        else:
            parties[key] = cs.key("gb.party", key)
            if parties[key] in cs.minted:
                cs.add("party", id=parties[key], kind="party", polity_id=cs.polity("GB"))
                cs.add(
                    "name",
                    entity_id=parties[key],
                    text=umit.NAMES.get(key, label),
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
    return parties[key]


def emit() -> Path:
    if "SCOT_BY" in os.environ:
        return emit_by()
    if "SCOT_YEAR" in os.environ:
        return emit_hist()
    units = pl.read_parquet(PARSED / "units.parquet").filter(pl.col("status") == "loaded")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    shapes = {code: (geom, point) for code, geom, point in pl.read_parquet(PARSED / "shapes.parquet").rows()}
    names = {
        p["LAD22CD"]: p["LAD22NM"]
        for url in ons_local.pages("wd22")
        for feature in json.loads(raw.cached(ons_local.SOURCE, url).path.read_text())["features"]
        if (p := feature["properties"])["WD22CD"].startswith("S13")
    }
    emb = pl.read_excel(raw.cached(SOURCE, EMB).path, sheet_name="RawDetails", engine="calamine")
    spellings = {
        word.lower(): word
        for label in emb["CandidateName"].drop_nulls().to_list()
        for word in str(label).split()
        if not word.isupper() and not word.islower()
    }
    authorities = {}
    for authority in emb["Authority Name"].unique().to_list():
        key = council_key(authority)
        authorities[next(code for code, label in names.items() if council_key(label) == key)] = authority
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish council elections of 5 May 2022 by ward with every STV stage, from the councils' eCount "
            "reports, tied to the Electoral Management Board's national results, with ONS ward and council shapes",
            polity="GB-SCT",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        scotland = cs.polity("GB-SCT")
        country = cs.area("gb.country", "SCT", DAY)
        system = cs.key("gb.electoral_system", "stv")
        if cs.minted & {country, system}:
            raise ValueError("publish gb.umit and the stv electoral system first")
        parties: dict[str, UUID] = {}

        emb_source = cs.source("Electoral Management Board for Scotland", "Local Government Elections 2022 - Results")
        cs.add(
            "source",
            id=emb_source,
            kind="official_body",
            publisher="Electoral Management Board for Scotland",
            title="Local Government Elections 2022 - Results",
            url=EMB,
            license="Open Government Licence v3.0",
            license_url=OGL,
            attribution="Electoral Management Board for Scotland",
            notes="The national results workbook (updated 13 May 2022 with the postal and polling station split); its "
            "RawDetails sheet has one row per candidate per ward with first preferences, the elected, and the stage "
            "each was elected at.",
        )
        cs.artifact(raw.cached(SOURCE, EMB), emb_source)
        wd22 = cs.source("Office for National Statistics", ons_local.LAYERS["wd22"][4])
        lad22 = cs.source("Office for National Statistics", ons_local.LAYERS["lad22"][4])
        if cs.minted & {wd22, lad22}:
            raise ValueError("publish gb.ons_local first")
        paths = {path for (path,) in conn.execute("select path from slug where path like 'gb/%%'").fetchall()}
        national = cs.key("gb.election", f"{DAY}:local:sct")
        cs.add("election", id=national, polity_id=scotland, kind="local", date_from=DAY, date_to=DAY, status="held")
        name(national, "2022 Scottish local elections")
        cs.add("slug", path=f"gb/scotland/{DAY}-local", entity_id=national)
        councils: dict[str, tuple[UUID, UUID, UUID, UUID, str, UUID]] = {}
        for code in sorted(set(units["council_code"])):
            label = names[code]
            authority = authorities[code]
            area = cs.area("gb.lad", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.lad")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=country, valid_from=None, valid_to=None)
                name(area, label)
                geom, point = shapes[code]
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=DAY,
                    valid_to=None,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=lad22,
                )
            polity = cs.key("gb.polity", code)
            path = f"gb/{slug(label)}"
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                cs.add("polity_parent", polity_id=polity, parent_id=scotland)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                if path in paths:
                    path = f"gb/{slug(label)}-council"
                cs.add("slug", path=path, entity_id=polity)
            else:
                current = conn.execute(
                    "select path from slug where entity_id = %s order by is_current desc limit 1", [polity]
                ).fetchone()
                if current is None:
                    raise ValueError(f"{code} has no slug")
                path = current[0]
            name(polity, authority)
            body = cs.key("gb.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
            name(body, authority)
            seats = int(units.filter(pl.col("council_code") == code)["seats"].sum())
            cs.add(
                "body_size",
                body_id=body,
                seats=seats,
                valid_from=DAY,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
            )
            election = cs.key("gb.election", f"{DAY}:local:{code}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=national,
                kind="local",
                date_from=DAY,
                date_to=DAY,
                status="held",
            )
            name(election, f"2022 {authority} election")
            cs.add("slug", path=f"{path}/{DAY}-local", entity_id=election)
            council_page = next(
                f"{MIRROR}/20220505_ME/pages/{found}/{found}.html"
                for found in re.findall(
                    r'20220505_ME/pages/(C\d\d_[^/"]+)/', raw.cached(SOURCE, INDEX).path.read_text()
                )
                if council_key(found) == council_key(label)
            )
            source = cs.source(authority, "Local government election 5 May 2022: eCount reports")
            checked = CHECKED.get(code)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=authority,
                title="Local government election 5 May 2022: eCount reports",
                url=council_page,
                license="Open Government Licence v3.0",
                license_url=OGL,
                attribution=authority,
                notes=(
                    "The returning officer's Candidate Votes per Stage report for each ward, as produced by the "
                    "Scottish eCount system, retrieved from the stv-results.prorep.org.uk mirror of the councils' "
                    "published files"
                    + (f"; the mirror's copy matched the council's own ({checked}) byte for byte." if checked else ".")
                ),
            )
            councils[code] = (area, polity, body, election, path, source)

        for unit_row in units.sort("ward_code").iter_rows(named=True):
            code, ward_code, label = unit_row["council_code"], unit_row["ward_code"], unit_row["ward_name"]
            area, polity, body, election, path, source = councils[code]
            ward = cs.area("gb.ward", ward_code)
            if ward in cs.minted:
                cs.add("area", id=ward, scheme_code="gb.ward")
                cs.add("area_code", area_id=ward, code=ward_code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=ward, parent_id=area, valid_from=None, valid_to=None)
                name(ward, label)
                geom, point = shapes[ward_code]
                cs.add(
                    "area_geometry",
                    area_id=ward,
                    valid_from=DAY,
                    valid_to=None,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=wd22,
                )
            unit = cs.unit("gb.ward", ward_code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="gb.ward",
                    code=ward_code,
                    area_relation="equals",
                    area_id=ward,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, label)
            contest_key = f"{DAY}:local:{ward_code}"
            contest = cs.key("gb.contest", contest_key)
            uncontested = unit_row["uncontested"]
            emb_only = unit_row["emb_only"] is True
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=ward,
                magnitude=unit_row["seats"],
                round_number=1,
                electoral_system_id=system,
                default_result_kind="tacit" if uncontested else "counted",
            )
            name(contest, f"{label} ward, {authorities[code]}, 2022")
            cs.add("slug", path=f"{path}/{DAY}-local/{slug(label)}", entity_id=contest)
            result_set = cs.key("gb.scot_council.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if uncontested else "counted",
                status="active",
                source_id=emb_source if uncontested or emb_only else source,
                is_default=True,
                completeness=1,
            )
            if uncontested:
                cs.add("citation", entity_id=result_set, source_id=emb_source, locator={"ward": unit_row["emb_ward"]})
                if unit_row["emb_ward"] in SEATS:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="context",
                        body_md=f"The ward returns {unit_row['seats']} councillors under the [Na h-Eileanan an Iar "
                        f"(Electoral Arrangements) Regulations 2021]({ARRANGEMENTS}), but only one candidate was "
                        "nominated, so one seat was left vacant."
                        if unit_row["emb_ward"] not in VACANT
                        else f"The ward returns {unit_row['seats']} councillors, but {VACANT[unit_row['emb_ward']]}.",
                    )
            elif emb_only:
                cs.add("citation", entity_id=result_set, source_id=emb_source, locator={"ward": unit_row["emb_ward"]})
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The council's stage report (Candidate Votes per Stage) was not found, so this ward "
                    "shows the Electoral Management Board's electorate, ballots, first preferences, and declared "
                    "members, with the stage at which each was elected; transfers are not included.",
                )
            else:
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=source,
                    locator={"ward": unit_row["emb_ward"], "file": unit_row["locator"]},
                )
                cs.add("citation", entity_id=result_set, source_id=emb_source, locator={"ward": unit_row["emb_ward"]})
                cs.artifact(raw.cached(SOURCE, unit_row["locator"]), source)
                checked = CHECKED.get(code)
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md="The stages are the council's own eCount report (Candidate Votes per Stage), retrieved "
                    "from the stv-results.prorep.org.uk mirror"
                    + (
                        ", which matched the council's copy byte for byte where checked."
                        if checked
                        else "; the council's own copy could not be reached to compare."
                    )
                    + " Stage 1 equals the Electoral Management Board's first preferences, the elected equal its "
                    "declared members, and every stage sums to the valid votes.",
                )
            if not uncontested:
                for measure, amount in (
                    (REGISTERED, unit_row["electorate"]),
                    (BALLOTS, unit_row["ballots"]),
                    (VALID, unit_row["valid"]),
                    (INVALID, unit_row["invalid"]),
                    (QUOTA, unit_row["quota"]),
                ):
                    if amount is None:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=Decimal(str(amount)),
                    )
            lines = totals.filter(pl.col("ward_code") == ward_code)
            for candidate in (
                lines.filter(pl.col("number") > 0)
                .select("number", "name", "party", "elected", "outcome", "stage_elected")
                .unique(maintain_order=True, subset=["number"])
                .iter_rows(named=True)
            ):
                owner = party_of(cs, parties, candidate["party"])
                full = " ".join(
                    spellings.get(
                        word.lower(),
                        re.sub(
                            r"\b(Mc)([a-z])",
                            lambda found: found.group(1) + found.group(2).upper(),
                            re.sub(r"[^\W\d_]+", lambda part: part.group(0).capitalize(), word),
                        ),
                    )
                    if word.isupper() and len(word) > 1
                    else word
                    for word in candidate["name"].split()
                )
                option_key = f"{contest_key}:{candidate['number']}"
                option = cs.key("gb.scot_council.option", option_key)
                person = cs.key("gb.scot_council.person", option_key)
                cs.add("person", id=person)
                name(person, full)
                cs.add(
                    "contest_option", id=option, contest_id=contest, kind="candidate", party_id=owner, ballot_label=full
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.scot_council.candidacy", option_key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate["name"],
                    member_of_party_id=owner,
                    is_independent=candidate["party"] == "Independent",
                )
                if not uncontested:
                    for row in (
                        lines.filter(pl.col("number") == candidate["number"]).sort("stage").iter_rows(named=True)
                    ):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=row["stage"],
                            flags=0,
                            value=Decimal(row["votes"]),
                        )
                        if Decimal(row["votes"]) == 0 and row["stage"] > 1 and not candidate["elected"]:
                            break
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=candidate["outcome"],
                    notes=f"Elected at stage {candidate['stage_elected']}."
                    if emb_only and candidate["elected"]
                    else None,
                )
            for row in lines.filter(pl.col("number") == 0).iter_rows(named=True):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=EXHAUSTED,
                    count_round=row["stage"],
                    flags=0,
                    value=Decimal(row["votes"]),
                )
        if (
            conn.execute("select 1 from boundary_set where scheme_code = 'gb.ward' and vintage = '2022'").fetchone()
            is None
        ):
            raise ValueError("the gb.ward 2022 boundary set is missing")
        return cs.write()


BY_ELECTIONS = Path(__file__).with_name("scot_by_elections.tsv")
BY = PARSED / "by"
FIFE_PAGE = "https://www.fife.gov.uk/council-and-democracy/elections-and-voting/election-results/local-government-election-results"
FIFE_SAVED = DATA / "inbox" / "manual" / "MF-642" / "fife-local-government-by-election-results.html"
FIFE_FIRST = (
    "local.fife.inverkeithing-and-dalgety-bay.by.2018-09-06",
    "local.fife.dunfermline-central.by.2019-11-14",
    "local.fife.rosyth.by.2019-11-14",
)
RECODED = {"S12000015": "S12000047", "S12000024": "S12000048", "S12000044": "S12000050", "S12000046": "S12000049"}
DESCRIPTIONS = (
    (r"independent green voice", "Independent Green Voice"),
    (r"scottish national|\bsnp\b", "Scottish National Party"),
    (r"conservative", "Scottish Conservative and Unionist Party"),
    (r"liberal democrat", "Scottish Liberal Democrats"),
    (r"labour", "Scottish Labour Party"),
    (r"green", "Scottish Green Party"),
    (r"^alba", "Alba Party"),
    (r"scottish family", "Scottish Family Party"),
    (r"libertarian", "Scottish Libertarian Party"),
    (r"trade unionist and socialist|\btusc\b", "Scottish Trade Unionist and Socialist Coalition"),
    (r"independence for scotland", "Independence for Scotland Party"),
    (r"uk independence|\bukip\b", "UK Independence Party"),
    (r"scottish socialist", "Scottish Socialist Party"),
    (r"^reform ?uk", "Reform UK"),
    (r"^restore britain", "Restore Britain"),
    (r"^your party", "Your Party"),
    (r"eco.?federalist", "Scottish Eco-Federalist Party"),
    (r"for britain", "For Britain Movement"),
    (r"alliance for democracy and freedom", "Alliance for Democracy and Freedom"),
    (r"edinburgh and east lothian people", "Edinburgh & East Lothian People"),
    (r"progressive change north lanarkshire", "Progressive Change North Lanarkshire"),
    (r"freedom alliance", "Freedom Alliance- Integrity, Society, Economy"),
    (r"sovereignty", "Sovereignty"),
    (r"vanguard", "Vanguard Party"),
    (r"social democratic", "Social Democratic Party"),
    (r"communist party", "Communist Party of Britain"),
    (r"workers party", "Workers Party of Britain"),
    (r"british unionist", "British Unionist Party"),
    (r"abolish holyrood|abolish the scottish parliament", "Abolish the Scottish Parliament Party"),
    (r"alliance to liberate scotland", "Alliance to Liberate Scotland"),
    (r"^independent$|^independent trade union and socialist candidate$|^$", "Independent"),
)


def national(description: str | None) -> str | None:
    text = fold(description or "")
    return next((label for pattern, label in DESCRIPTIONS if re.search(pattern, text)), None)


TEXT_REPAIRS = {"ártainn MAC A' BHÀILLIDH": "Mártainn MAC A' BHÀILLIDH"}


def pdf_text(path: Path) -> str:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    for broken, whole in TEXT_REPAIRS.items():
        text = re.sub(rf"(?<!\S){re.escape(broken)}", whole, text)
    return text


def declares(text: str, label: str, votes: Decimal) -> bool:
    lines = text.splitlines()
    return any(
        Decimal(number.replace(",", "")) == votes
        for index, line in enumerate(lines)
        if re.match(rf"\s*{re.escape(label)}(\s|$)", line, re.IGNORECASE)
        for number in re.findall(r"\d[\d,]*", " ".join(lines[max(index - 1, 0) : index + 2]))
    )


def fetch_by() -> None:
    try:
        raw.cached(SOURCE, FIFE_PAGE)
    except LookupError:
        raw.store(
            SOURCE,
            FIFE_PAGE,
            FIFE_SAVED.read_bytes(),
            suffix=".html",
            retrieved_at=datetime.fromtimestamp(FIFE_SAVED.stat().st_mtime, UTC),
            media_type="text/html",
        )
    for row in pl.read_csv(BY_ELECTIONS, separator="\t", infer_schema=False).iter_rows(named=True):
        for url in (row["stages"], row["declaration"]):
            if url is None:
                continue
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                try:
                    raw.fetch(SOURCE, url, suffix=".pdf")
                except httpx.HTTPError as error:
                    print(f"{row['ballot']}: {url}: {error}")
                time.sleep(1)


def read_first(text: str, ward: str) -> tuple[list[tuple[str, str, Decimal]], str]:
    plain = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?s)<(script|style).*?</\1>", "", text)))
    lines = [" ".join(line.replace("&", "and").split()) for line in plain.splitlines() if line.strip()]
    start = next(
        index + 1
        for index, line in enumerate(lines)
        if re.fullmatch(
            rf"The results for the Local Government By-elections? for (the )?{re.escape(ward)} council ward are:", line
        )
    )
    end = next(index for index in range(start, len(lines)) if "declared as the newly elected" in lines[index])
    body = [
        line
        for line in lines[start : end - 1]
        if not re.match(r"(Candidate Name|Party|No\. of |st preference votes)", line)
    ]
    winner = lines[end - 1].split(",")[0].strip()
    rows = []
    for index in range(0, len(body) - 2, 3):
        rows.append((body[index], body[index + 1], Decimal(body[index + 2].replace(",", ""))))
    if len(body) % 3 or winner not in {name for name, _, _ in rows}:
        raise ValueError(f"{ward}: first preference table not read: {body[:6]} {winner}")
    return rows, winner


def parse_by() -> None:
    wards = {
        row["ballot"]: row
        for path in sorted((DATA / "parsed" / "gb.dc_local" / "by").glob("*-wards.parquet"))
        for row in pl.read_parquet(path).filter(pl.col("territory") == "SCT").iter_rows(named=True)
    }
    units: list[dict[str, str | int | date | None]] = []
    totals: list[dict[str, str | int | bool | None]] = []
    for row in pl.read_csv(BY_ELECTIONS, separator="\t", infer_schema=False).iter_rows(named=True):
        ward = wards[row["ballot"]]
        key = f"{ward['day']}:local:{ward['ward_code']}:by"
        unit: dict[str, str | int | date | None] = {
            "key": key,
            "ballot": row["ballot"],
            "day": ward["day"],
            "council_code": RECODED.get(ward["la_code"], ward["la_code"]),
            "ward_code": ward["ward_code"],
            "ward_name": ward["ons_name"],
            "stages": row["stages"],
            "declaration": row["declaration"],
        }
        units.append(unit)
        if row["stages"] is None:
            unit["status"] = row["note"] or "no stage report"
            continue
        stages_text = pdf_text(raw.cached(SOURCE, row["stages"]).path)
        declared = pdf_text(raw.cached(SOURCE, row["declaration"]).path) if row["declaration"] else ""
        facts, rows, exhausted, dropped, odd, affiliations = read_stages(stages_text)
        if not rows or odd:
            other = read_other(stages_text, declared)
            if other[1]:
                facts, rows, exhausted, dropped, odd, affiliations = other
                facts.setdefault("seats", Decimal(ward["seats"] or 1))
                if "quota" not in facts and "valid" in facts:
                    facts["quota"] = facts["valid"] // (facts["seats"] + 1) + 1
        counts: dict[str, int | None] = {}
        for name, pattern in (
            ("electorate", r"electorate was ([\d,]+)"),
            ("ballots", r"ballot papers received was ([\d,]+)"),
            ("invalid", r"[Tt]here\s+were\s+([\d,]+)\s+rejected"),
        ):
            found = re.search(pattern, declared)
            counts[name] = int(found.group(1).replace(",", "")) if found else None
        if (found := re.search(r"There\s+was\s+1\s+rejected", declared)) is not None:
            counts["invalid"] = 1
        if counts["ballots"] is None and (
            found := re.search(r"Total number of Ballot Papers Received\s+([\d,]+)", stages_text)
        ):
            counts["ballots"] = int(found.group(1).replace(",", ""))
        for name, key_ in (("electorate", "electorate"), ("ballots", "cast"), ("invalid", "rejected")):
            if counts[name] is None and key_ in facts:
                counts[name] = int(facts[key_])
        unit |= counts
        ballots, invalid = counts["ballots"], counts["invalid"]
        names = [label for number, label, _, _ in rows if number == 1]
        elected = {label for _, label, _, outcome in rows if outcome == "Elected"}
        stages = sorted({number for number, _, _, _ in rows})
        status = "loaded"
        if odd:
            status = f"unread lines: {odd[:3]}"
        elif not names or "valid" not in facts:
            status = "no stages read"
        elif len(elected) != facts.get("seats"):
            status = "elected do not fill the seats"
        elif ballots is not None and invalid is not None and ballots - invalid != facts["valid"]:
            status = "ballots less rejected differ from the valid votes"
        elif declared and any(
            not (
                declares(declared, label, votes)
                or (
                    "Stage No" in declared
                    and declares(declared, label, max(at for _, other, at, _ in rows if other == label))
                )
            )
            for number, label, votes, _ in rows
            if number == 1
        ):
            status = "stage 1 differs from the declaration"
        else:
            for number in stages:
                total = exhausted.get(number, Decimal(0)) + sum(votes for at, _, votes, _ in rows if at == number)
                if total != facts["valid"]:
                    status = f"stage {number} sums to {total}, not {facts['valid']}"
                    break
            for candidate in sorted(elected):
                if status != "loaded":
                    break
                best = max(votes for _, label, votes, _ in rows if label == candidate)
                when = min(number for number, label, _, outcome in rows if label == candidate and outcome == "Elected")
                if best < facts["quota"] and when != stages[-1]:
                    status = f"{candidate} elected below the quota before the last stage"
        unit |= {
            "status": status,
            "seats": int(facts["seats"]) if "seats" in facts else None,
            "valid": str(facts.get("valid")),
            "quota": str(facts.get("quota")),
        }
        missing = [label for label in names if national(affiliations.get(label)) is None]
        if status == "loaded" and missing:
            unit["status"] = f"unmapped descriptions: {[affiliations.get(label) for label in missing]}"
        if unit["status"] != "loaded":
            continue
        order = {label: number for number, label in enumerate(names, start=1)}
        when = {
            label: min(number for number, other, _, outcome in rows if other == label and outcome == "Elected")
            for label in elected
        }
        for number, label, votes, _ in rows:
            totals.append(
                {
                    "key": key,
                    "number": order[label],
                    "name": label,
                    "description": affiliations.get(label),
                    "party": national(affiliations.get(label)),
                    "elected": label in elected,
                    "outcome": "elected"
                    if label in elected
                    else ("eliminated" if name_key(label) in dropped else "lost"),
                    "stage": number,
                    "stage_elected": when.get(label),
                    "votes": str(votes),
                }
            )
        for number in stages:
            totals.append({"key": key, "number": 0, "stage": number, "votes": str(exhausted.get(number, Decimal(0)))})
    page = raw.cached(SOURCE, FIFE_PAGE).path.read_text()
    for ballot in FIFE_FIRST:
        ward = wards[ballot]
        rows, winner = read_first(page, ward["ons_name"])
        key = f"{ward['day']}:local:{ward['ward_code']}:by"
        units.append(
            {
                "key": key,
                "ballot": ballot,
                "day": ward["day"],
                "council_code": RECODED.get(ward["la_code"], ward["la_code"]),
                "ward_code": ward["ward_code"],
                "ward_name": ward["ons_name"],
                "stages": FIFE_PAGE,
                "status": "loaded",
                "seats": int(ward["seats"]),
                "valid": str(sum(votes for _, _, votes in rows)),
                "first_only": True,
            }
        )
        for number, (label, description, votes) in enumerate(rows, start=1):
            elected = label == winner
            totals.append(
                {
                    "key": key,
                    "number": number,
                    "name": label,
                    "description": description,
                    "party": national(description),
                    "elected": elected,
                    "outcome": "elected" if elected else "lost",
                    "stage": 1,
                    "votes": str(votes),
                }
            )
    frame = pl.DataFrame(units, infer_schema_length=None)
    print(frame.group_by("status").len().sort("len", descending=True).rows())
    BY.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(BY / "units.parquet")
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(BY / "totals.parquet")


def emit_by() -> Path:
    units = pl.read_parquet(BY / "units.parquet").filter(pl.col("status") == "loaded")
    totals = pl.read_parquet(BY / "totals.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish council by-elections since May 2017 by ward with every STV stage, from the councils' "
            "count reports",
            polity="GB-SCT",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def held(entity: UUID, what: str) -> UUID:
            if entity in cs.minted:
                raise ValueError(f"{what} is not held; publish gb.scot_council and gb.leap_scot first")
            return entity

        system = held(cs.key("gb.electoral_system", "stv"), "the stv electoral system")
        parties: dict[str, UUID] = {}
        elections: set[UUID] = set()
        for unit_row in units.sort("day", "ward_code").iter_rows(named=True):
            code, ward_code, label, day = (
                unit_row["council_code"],
                unit_row["ward_code"],
                unit_row["ward_name"],
                unit_row["day"],
            )
            polity = held(cs.key("gb.polity", code), code)
            body = held(cs.key("gb.body", code), code)
            found = conn.execute(
                "select s.path, n.text from slug s join name n on n.entity_id = %s and n.is_preferred "
                "where s.entity_id = %s order by s.is_current desc limit 1",
                [body, polity],
            ).fetchone()
            if found is None:
                raise ValueError(f"{code} has no slug")
            path, authority = found
            election = cs.key("gb.election", f"{day}:local:{code}")
            if election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind="by_election",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{authority} by-election, {day.day} {day:%B %Y}")
                cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
            ward = held(cs.area("gb.ward", ward_code), ward_code)
            unit = held(cs.unit("gb.ward", ward_code), ward_code)
            contest_key = unit_row["key"]
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=ward,
                magnitude=unit_row["seats"],
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
                term="remainder",
            )
            name(contest, f"{label} ward by-election, {authority}, {day.year}")
            cs.add("slug", path=f"{path}/{day}-local/{slug(label)}-by-election", entity_id=contest)
            cs.add("external_id", entity_id=contest, scheme="democracyclub.ballot", value=unit_row["ballot"])
            first_only = bool(unit_row["first_only"])
            title = f"{label} ward by-election, {day.day} {day:%B %Y}: " + (
                "first preferences" if first_only else "count reports"
            )
            source = cs.source(authority, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=authority,
                title=title,
                url=unit_row["stages"],
                license="Open Government Licence v3.0",
                license_url=OGL,
                attribution=authority,
                notes="The council's results page, which prints each candidate's first preference votes and the "
                "member declared elected, with no stage table."
                if first_only
                else "The returning officer's Candidate Votes per Stage report"
                + (" and Declaration of Results" if unit_row["declaration"] else "")
                + ", as published by the council.",
            )
            result_set = cs.key("gb.scot_council.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            for url in (unit_row["stages"], unit_row["declaration"]):
                if url is not None:
                    cs.artifact(raw.cached(SOURCE, url), source)
                    cs.add("citation", entity_id=result_set, source_id=source, locator={"file": url})
            for measure, amount in (
                (REGISTERED, unit_row["electorate"]),
                (BALLOTS, unit_row["ballots"]),
                (VALID, unit_row["valid"]),
                (INVALID, unit_row["invalid"]),
                (QUOTA, unit_row["quota"]),
            ):
                if amount is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=Decimal(str(amount)),
                    )
            lines = totals.filter(pl.col("key") == contest_key)
            for candidate in (
                lines.filter(pl.col("number") > 0).unique(maintain_order=True, subset=["number"]).iter_rows(named=True)
            ):
                owner = party_of(cs, parties, candidate["party"])
                full = " ".join(
                    re.sub(
                        r"\b(Mc|Mac|O')([a-z])",
                        lambda found: found.group(1) + found.group(2).upper(),
                        re.sub(r"[^\W\d_']+", lambda part: part.group(0).capitalize(), word),
                    )
                    if word.isupper() and len(word) > 1
                    else word
                    for word in candidate["name"].split()
                )
                option_key = f"{contest_key}:{candidate['number']}"
                option = cs.key("gb.scot_council.option", option_key)
                person = cs.key("gb.scot_council.person", option_key)
                cs.add("person", id=person)
                name(person, full)
                cs.add(
                    "contest_option", id=option, contest_id=contest, kind="candidate", party_id=owner, ballot_label=full
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.scot_council.candidacy", option_key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate["name"],
                    member_of_party_id=owner,
                    is_independent=candidate["party"] == "Independent",
                )
                for row in lines.filter(pl.col("number") == candidate["number"]).sort("stage").iter_rows(named=True):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=row["stage"],
                        flags=0,
                        value=Decimal(row["votes"]),
                    )
                    if Decimal(row["votes"]) == 0 and row["stage"] > 1 and not candidate["elected"]:
                        break
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=candidate["outcome"],
                    notes=f"Elected at stage {candidate['stage_elected']}."
                    if candidate["elected"] and not first_only
                    else None,
                )
            for row in lines.filter(pl.col("number") == 0).iter_rows(named=True):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=EXHAUSTED,
                    count_round=row["stage"],
                    flags=0,
                    value=Decimal(row["votes"]),
                )
        return cs.write()


STAGES = Path(__file__).with_name("scot_council_stages.tsv")
HIST = PARSED / "hist"


def stage_rows() -> list[dict[str, str]]:
    year = os.environ["SCOT_YEAR"]
    return [
        row
        for row in pl.read_csv(STAGES, separator="\t", infer_schema=False).iter_rows(named=True)
        if row["year"] == year
    ]


def xls_text(path: Path) -> str:
    sheet = pl.read_excel(path, engine="calamine", has_header=False)
    return "\n".join(
        "  ".join(re.sub(r"\s+", " ", str(cell)).strip() for cell in row if cell is not None and str(cell).strip())
        for row in sheet.iter_rows()
    )


def fetch_hist() -> None:
    for row in stage_rows():
        try:
            raw.cached(SOURCE, row["url"])
        except LookupError:
            try:
                raw.fetch(
                    SOURCE,
                    row["url"],
                    suffix=".xls"
                    if re.search(r"highland\.gov\.uk/downloads/file/.*election-results", row["url"])
                    else ".pdf",
                )
            except httpx.HTTPError as error:
                print(f"{row['council']} {row['ward']}: {row['url']}: {error}")
            time.sleep(4 if "web.archive.org" in row["url"] else 1)


def parse_hist() -> None:
    from ea.sources.gb import leap_scot

    year = int(os.environ["SCOT_YEAR"])
    held = pl.read_parquet(leap_scot.PARSED / "candidates.parquet").filter(pl.col("year") == year)
    contests: dict[tuple[str, tuple[int, ...] | frozenset[str]], list[str]] = {}
    for (council, ward_code), lines in held.group_by("council", "ward_code"):
        if lines["votes"].sum():
            contests.setdefault((council, tuple(sorted(lines["votes"].to_list()))), []).append(ward_code)
            contests.setdefault((council, frozenset(name_key(name) for name in lines["name"])), []).append(ward_code)
    units: list[dict[str, str | int | None]] = []
    totals: list[dict[str, str | int | bool | None]] = []
    taken: set[str] = set()
    for row in stage_rows():
        council, locator = row["council"], row["url"]
        try:
            path = raw.cached(SOURCE, locator).path
        except LookupError:
            units.append({"council": council, "ward": row["ward"], "locator": locator, "status": "not fetched"})
            continue
        text = xls_text(path) if path.suffix == ".xls" else pdf_text(path)
        if "Number to be elected" in text and "valid votes" in text.lower():
            facts, rows, exhausted, dropped, odd, _ = read_estv(path, text)
        elif "Total Number of Valid Votes" in text and "Number of Vacancies" in text:
            facts, rows, exhausted, dropped, odd, _ = read_wide(text)
        else:
            facts, rows, exhausted, dropped, odd, _ = read_stages(text)
        ballots = re.search(r"(?:Ballot Papers|Total Number of Ballots) Received\s+(" + NUMBER + ")", text)
        first = [(label, votes, outcome) for number, label, votes, outcome in rows if number == 1]
        found = contests.get((council, tuple(sorted(int(votes) for _, votes, _ in first))), [])
        if not found:
            found = contests.get((council, frozenset(name_key(label) for label, _, _ in first)), [])
        found = [code for code in found if code not in taken]
        status = "loaded"
        ward_code = found[0] if len(found) == 1 else None
        lines = held.filter(pl.col("ward_code") == ward_code).sort("number") if ward_code else held.clear()
        by_name = {name_key(line["name"]): line for line in lines.iter_rows(named=True)}
        by_votes = {
            (line["votes"], name_key(line["name"].split()[-1]) if line["votes"] in tied else ""): line
            for tied in [{votes for votes in lines["votes"].to_list() if lines["votes"].to_list().count(votes) > 1}]
            for line in lines.iter_rows(named=True)
        }
        matched = {
            label: line
            for label, votes, _ in first
            if (
                line := by_name.get(name_key(label))
                or by_votes.get((int(votes), ""))
                or by_votes.get((int(votes), name_key(label.split()[-1])))
            )
            is not None
        }
        elected = {label for _, label, _, outcome in rows if outcome == "Elected"}
        stages = sorted({number for number, _, _, _ in rows})
        if odd:
            status = f"unread lines: {odd[:3]}"
        elif ward_code is None:
            status = f"{len(found)} wards with these first preferences"
        elif len(matched) != len(first) or len({line["number"] for line in matched.values()}) != len(first):
            status = "candidates do not match one to one"
        elif elected != {label for label, line in matched.items() if line["elected"]}:
            status = "elected differ from the first preferences held"
        elif len(elected) != facts.get("seats"):
            status = "elected do not fill the seats"
        else:
            for number in stages:
                total = exhausted.get(number, Decimal(0)) + sum(
                    (votes for at, _, votes, _ in rows if at == number), Decimal(0)
                )
                if total != facts["valid"]:
                    status = f"stage {number} sums to {total}, not {facts['valid']}"
                    break
        differs = "; ".join(
            f"{label} {votes} for {matched[label]['votes']}"
            for label, votes, _ in first
            if status == "loaded" and int(votes) != matched[label]["votes"]
        )
        if ward_code and status == "loaded":
            taken.add(ward_code)
        units.append(
            {
                "council": council,
                "ward": row["ward"],
                "locator": locator,
                "status": status,
                "ward_code": ward_code,
                "seats": int(facts["seats"]) if "seats" in facts else None,
                "ballots": str(Decimal(ballots.group(1).replace(",", ""))) if ballots else None,
                "valid": str(facts.get("valid")),
                "quota": str(facts.get("quota")),
                "differs": differs or None,
            }
        )
        if status != "loaded":
            continue
        for number, label, votes, _ in rows:
            line = matched[label]
            totals.append(
                {
                    "ward_code": ward_code,
                    "number": line["number"],
                    "name": label,
                    "elected": label in elected,
                    "outcome": "elected"
                    if label in elected
                    else ("eliminated" if name_key(label) in dropped else "lost"),
                    "stage": number,
                    "votes": str(votes),
                }
            )
        for number in stages:
            totals.append(
                {
                    "ward_code": ward_code,
                    "number": 0,
                    "name": None,
                    "elected": False,
                    "outcome": None,
                    "stage": number,
                    "votes": str(exhausted.get(number, Decimal(0))),
                }
            )
    frame = pl.DataFrame(units, infer_schema_length=None)
    print(frame.group_by("status").len().sort("len", descending=True).rows())
    wanted = held.filter(pl.col("votes") > 0, pl.col("council").is_in({row["council"] for row in stage_rows()}))
    print("unmatched", sorted(set(wanted["ward_code"]) - taken))
    HIST.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(HIST / f"units{year}.parquet")
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(HIST / f"totals{year}.parquet")


def emit_hist() -> Path:
    from ea.sources.gb import leap_scot

    year = int(os.environ["SCOT_YEAR"])
    day = leap_scot.DAYS[year]
    units = pl.read_parquet(HIST / f"units{year}.parquet").filter(pl.col("status") == "loaded")
    totals = pl.read_parquet(HIST / f"totals{year}.parquet")
    held = pl.read_parquet(leap_scot.PARSED / "candidates.parquet").filter(pl.col("year") == year)
    pages = {row["council"]: row["page"] for row in stage_rows()}
    report = "final count report" if year == 2007 else "eCount report"
    system = (
        "the eSTV count software of the electronic count"
        if year == 2007
        else "the Scottish eCount system (Candidate Votes per Stage)"
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Scottish council elections of {day.day} {day:%B %Y} by ward with every STV stage, from the "
            f"councils' {report}s, superseding the first preferences from the Local Elections Archive Project",
            polity="GB-SCT",
        )

        def held_key(entity: UUID, what: str) -> UUID:
            if entity in cs.minted:
                raise ValueError(f"{what} is not held; publish gb.leap_scot first")
            return entity

        sources: dict[str, UUID] = {}
        for unit_row in units.sort("ward_code").iter_rows(named=True):
            council, ward_code = unit_row["council"], unit_row["ward_code"]
            lines = held.filter(pl.col("ward_code") == ward_code).sort("number")
            code = lines["la_code"][0]
            body = held_key(cs.key("gb.body", code), code)
            election = held_key(cs.key("gb.election", f"{day}:local:{code}"), code)
            unit = held_key(cs.unit("gb.ward", ward_code), ward_code)
            contest_key = f"{day}:local:{ward_code}"
            contest = held_key(cs.key("gb.contest", contest_key), contest_key)
            named = conn.execute(
                "select n.text from name n where n.entity_id = %s and n.is_preferred", [body]
            ).fetchone()
            if named is None:
                raise ValueError(f"{code} has no name")
            authority = named[0]
            if council not in sources:
                title = f"Local government election {day.day} {day:%B %Y}: {report}s"
                sources[council] = cs.source(authority, title)
                cs.add(
                    "source",
                    id=sources[council],
                    kind="official_body",
                    publisher=authority,
                    title=title,
                    url=pages[council],
                    license="Open Government Licence v3.0",
                    license_url=OGL,
                    attribution=authority,
                    notes=f"The returning officer's stage-by-stage count report for each ward, as produced by "
                    f"{system} and published by the council.",
                )
            source = sources[council]
            previous = cs.key("gb.leap_scot.result_set", contest_key)
            for values in conn.execute(
                """select id, election_id, contest_id, stage, kind, source_id, as_of, completeness
                   from result_set where id = %s""",
                [previous],
            ).fetchall():
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            ("id", "election_id", "contest_id", "stage", "kind", "source_id", "as_of", "completeness"),
                            values,
                            strict=True,
                        )
                    ),
                    status="superseded",
                    is_default=False,
                )
            result_set = cs.key("gb.scot_council.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                supersedes_id=previous,
                completeness=1,
            )
            cs.add("citation", entity_id=result_set, source_id=source, locator={"file": unit_row["locator"]})
            cs.artifact(raw.cached(SOURCE, unit_row["locator"]), source)
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=f"The stages are the council's own {report}, from {system}. Stage 1 equals "
                "the first preferences in the earlier set from the Local Elections Archive Project, the elected "
                "equal its elected, and every stage sums to the valid votes.",
            )
            for measure, amount in (
                (BALLOTS, unit_row["ballots"]),
                (VALID, unit_row["valid"]),
                (QUOTA, unit_row["quota"]),
            ):
                if amount is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=Decimal(amount),
                    )
            lines = totals.filter(pl.col("ward_code") == ward_code)
            for candidate in (
                lines.filter(pl.col("number") > 0)
                .select("number", "outcome")
                .unique(maintain_order=True, subset=["number"])
                .iter_rows(named=True)
            ):
                option = held_key(cs.key("gb.leap_scot.option", f"{contest_key}:{candidate['number']}"), contest_key)
                for row in lines.filter(pl.col("number") == candidate["number"]).sort("stage").iter_rows(named=True):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=row["stage"],
                        flags=0,
                        value=Decimal(row["votes"]),
                    )
                    if Decimal(row["votes"]) == 0 and row["stage"] > 1 and candidate["outcome"] != "elected":
                        break
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=candidate["outcome"])
            for row in lines.filter(pl.col("number") == 0).iter_rows(named=True):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=EXHAUSTED,
                    count_round=row["stage"],
                    flags=0,
                    value=Decimal(row["votes"]),
                )
        return cs.write()
