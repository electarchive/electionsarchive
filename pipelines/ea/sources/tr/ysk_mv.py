import base64
import difflib
import hashlib
import html
import io
import json
import re
import struct
import subprocess
import tempfile
import time
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import anthropic
import polars as pl
from anthropic.types.messages import MessageBatchIndividualResponse
from anthropic.types.messages.batch_create_params import Request
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.scans import LEDGER, dollars, ledger
from ea.sources.tr.tbmm import fold
from ea.sources.tr.thea import PARTIES
from ea.sources.tr.ysk import (
    DIGITIZED,
    INDEPENDENTS,
    LICENSE,
    PROVINCES,
    PUBLISHER,
    REGISTERED,
    VALID,
    VOTERS,
    VOTES,
)
from ea.store import DATA, connect

SOURCE = "tr.ysk_mv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
ARCHIVE = "https://www.resmigazete.gov.tr/arsiv"
ISSUES = {
    date(1965, 10, 10): (12130, date(1965, 10, 19), range(1, 13), 2),
    date(1969, 10, 12): (13331, date(1969, 10, 20), range(2, 15), 2),
    date(1973, 10, 14): (14698, date(1973, 10, 31), range(7, 24), 2),
    date(1977, 6, 5): (15971, date(1977, 6, 19), range(4, 49), 1),
}
TABLED = {date(1961, 10, 15): (10960, date(1961, 11, 17), range(7, 14), 1)}
EDGES = [815, 1085, 1316, 1645, 1885, 2155, 2427, 2696, 2915, 3243, 3570]
MODEL = "claude-haiku-5-5"
PROMPT = "1"
STOP = 320.0
SYSTEM = (
    "You transcribe one crop of a page of Turkey's Official Gazette (Resmî Gazete) from the 1960s or 1970s. The "
    "page prints the Supreme Election Council's results by province: a province heading, the number of registered "
    "voters, voters, turnout, valid ballots, each party's valid ballots, the independents' total, then the elected "
    "members with their parties. Other notices may share the page. Transcribe every printed line from top to bottom "
    "exactly as printed, in Turkish, without correcting anything. For each line give `left`, the text before the "
    "line's last column, and `right`, the last column: the number exactly as printed (keep its dots), or on an "
    "elected-member line the party or independent text (a ditto mark as printed, such as \u00bb). Use an empty "
    "`right` when the line has no last column. Do not skip, merge, or reorder lines, and do not guess text you "
    "cannot see; a line cut by the crop edge is transcribed as far as it is visible."
)
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"left": {"type": "string"}, "right": {"type": "string"}},
                "required": ["left", "right"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}
ALIASES = {
    "afyon": "afyonkarahisar",
    "icel": "mersin",
    "kmaras": "kahramanmaras",
    "maras": "kahramanmaras",
    "urfa": "sanliurfa",
}
KNOWN = {fold(name) for name in PROVINCES} | set(ALIASES)
NUMBER = re.compile(r"^[\dJ.,]+$")
PARTY_WORDS = (
    ("adalet", "AP"),
    ("cumhuriyetciguven", "CGP"),
    ("cumhuriyetcikoylu", "CKMP"),
    ("halk", "CHP"),
    ("selamet", "MSP"),
    ("milliyetci", "MHP"),
    ("hareket", "MHP"),
    ("guven", "GP"),
    ("demokratik", "DP"),
    ("isci", "TİP"),
    ("turkiyebirlik", "TBP"),
    ("birlik", "BP"),
    ("yeniturkiye", "YTP"),
    ("millet", "MP"),
    ("bagimsiz", "IND"),
)
ABBREVIATIONS = {
    "AP": "AP",
    "CHP": "CHP",
    "CGP": "CGP",
    "CKMP": "CKMP",
    "DP": "DP",
    "MP": "MP",
    "MSP": "MSP",
    "MHP": "MHP",
    "TBP": "TBP",
    "BP": "BP",
    "GP": "GP",
    "TIP": "TİP",
    "YTP": "YTP",
}
PARTY_START = {
    "adalet",
    "bagimsiz",
    "birlik",
    "cumhuriyet",
    "cumhuriyetci",
    "demokratik",
    "halk",
    "isci",
    "millet",
    "milli",
    "milliyetci",
    "parti",
    "partisi",
    "turkiye",
    "yeni",
}
FURNITURE = ("sahife", "sahile", "sayfa", "resmigazete", "resmigaze", "yargibolumu", "yurutmeveidare")
STOPS = (
    "itibariyle",
    "ilanolunur",
    "teblig",
    "mahkeme",
    "hakimlig",
    "aldigigecerli",
    "gecerlioy",
    "secmensayisi",
    "uyeleri",
    "secimi",
    "bildiri",
    "ilsecimcevrelerinden",
    "partilistesinde",
    "toplam",
)
DAMAGED = {
    (date(1969, 10, 12), "elazig"): [
        ("Faruk Sanaç", "AP"),
        ("Güldoğan", "AP"),
        ("Aytuğ", "CHP"),
        ("Hanağası", "CHP"),
        ("Septioğlu", "IND"),
    ],
    (date(1969, 10, 12), "erzincan"): [
        ("Perinçek", "AP"),
        ("Atabeyli", "AP"),
        ("Çetinkaya", "CHP"),
        ("Yıldırım", "CHP"),
    ],
    (date(1969, 10, 12), "erzurum"): [
        ("Danışman", "AP"),
        ("Cinisli", "AP"),
        ("Önder", "AP"),
        ("Gaciroğlu", "AP"),
        ("Bilgin", "AP"),
        ("Taşkesenlioğlu", "AP"),
        ("Aras", "AP"),
        ("Erverdi", "CHP"),
        ("Karaca", "CHP"),
    ],
}
REVISION_DAY = date(1965, 10, 10)
REVISION_ISSUE = 12142
REVISION_TITLE = "Yüksek Seçim Kurulu kararı 965/671, Resmî Gazete 12142 (04.11.1965)"
REVISION_NOTES = (
    "The Supreme Election Council's decision of 19 October 1965 on objections to its national remainder allocation: "
    "it voided the credentials of members wrongly declared elected on 14 and 17 October and issued new ones."
)
REVISION_NOTE = (
    "On 19 October 1965 the Supreme Election Council found an error in its national remainder allocation, voided "
    "the credentials it had issued for this province, and declared the members shown here (decision 965/671, "
    "Resmî Gazete 12142). Two seats were left to a drawing of lots between the parties' nominees: the Workers' "
    "Party seat in Malatya or Urfa, and the New Turkey Party seat in Adıyaman or Kars. The result of each drawing "
    "comes from the Grand National Assembly's album of members."
)
REVISED = {
    1: [("Ahmet Bilgin", "Ali Karcı", "TİP", 3)],
    3: [("Kemal Çıracı", "Muzaffer Özdağ", "CKMP", 4)],
    4: [("Nazmi Oğuz", "Abdülbari Akdoğan", "CKMP", 3)],
    17: [("Numan Esin", "Cihat Baban", "CHP", 4)],
    22: [("Sadun Aren", "Ahmet Bilgin", "MP", 4)],
    23: [("Sevinç Düşünsel", "Ömer Eken", "AP", 3)],
    32: [("Ömer Eken", "İrfan Aksu", "YTP", 3)],
    33: [("İlhan Tezel", "Kemal Ataman", "MP", 4)],
    34: [("Arif Hikmet Güner", "Sadun Aren", "TİP", 3)],
    36: [("İrfan Aksu", "Sevinç Düşünsel", "YTP", 4)],
    37: [("Cihat Baban", "İsmail Hakkı Yılanlıoğlu", "CKMP", 3)],
    39: [("Behice Hatko (Boran)", "Arif Hikmet Güner", "AP", 4)],
    44: [("Mehmet Hayrettin Abacı", "Şaban Erik", "TİP", 4)],
    47: [("Muzaffer Özdağ", "Nazmi Oğuz", "CHP", 4)],
    63: [("Hüseyin Kiraz", "Behice Hatko (Boran)", "TİP", 4)],
    66: [("Kemal Ataman", "Yusuf Ziya Bahadınlı", "TİP", 3)],
}
HEADER = {"gazete", "resmi", "sayi", "kurulu", "karari", "ekim", "haziran", "sali", "pazartesi", "carsamba"}
PARTY_KEYS = {
    "AP": "adalet-partisi-1961",
    "CHP": "cumhuriyet-halk-partisi",
    "CKMP": "cumhuriyetci-koylu-millet-partisi",
    "MHP": "cumhuriyetci-koylu-millet-partisi",
    "MP": "millet-partisi-1962",
    "YTP": "yeni-turkiye-partisi-1961",
    "CGP": "cumhuriyetci-guven-partisi",
    "GP": "guven-partisi",
    "DP": "demokratik-parti",
    "MSP": "milli-selamet-partisi",
    "BP": "turkiye-birlik-partisi",
    "TBP": "turkiye-birlik-partisi",
    "TİP": "turkiye-isci-partisi-1961",
}
TOTALS = {"registered": REGISTERED, "voters": VOTERS, "valid": VALID}
NOTES = (
    "The Supreme Election Council's second notice under Article 35 of Law 306: by province, registered voters, "
    "voters, turnout, valid ballots, each party's and the independents' valid ballots, and the elected members with "
    "their parties. Read from the gazette's text layer and a Haiku transcription of the page images; figures where "
    "the two readings differ were settled against THEA's figure."
)
NOTES_1961 = (
    "Decision 389 of the Supreme Election Council: section Ç lists the elected members by province with their "
    "parties, and section E gives, by province, registered voters, voters, turnout, valid ballots, and each party's "
    "and the independents' valid ballots. Read from the gazette's text layer and checked against the page images; "
    "where a printed digit is broken, the reading THEA and the State Institute of Statistics share was taken. The "
    "council published the returns as the provincial boards reported them, without correcting their addition errors, "
    "so some rows do not sum."
)
LETTERS = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]")
CLEA = {
    "AP": 17,
    "CHP": 40,
    "CKMP": 39,
    "MHP": 39,
    "MP": 21,
    "MSP": 22,
    "YTP": 29,
    "CGP": 41,
    "GP": 48,
    "DP": 7,
    "TİP": 47,
    "BP": 47,
    "TBP": 47,
    "independents": 6000,
}


def url(issue: int) -> str:
    return f"{ARCHIVE}/{issue}.pdf"


def fetch() -> None:
    for issue, *_ in (ISSUES | TABLED).values():
        raw.fetch(SOURCE, url(issue), suffix=".pdf")


def gutter(words: list[tuple[str, scan.Box]], width: int) -> int:
    cover = [0] * (width + 1)
    for _, box in words:
        for x in range(max(int(box[0]), 0), min(int(box[2]) + 1, width + 1)):
            cover[x] += 1
    return min(range(int(width * 0.35), int(width * 0.65)), key=lambda x: (cover[x], abs(x - width / 2)))


def crops() -> list[tuple[date, int, int, int, str, str, bytes]]:
    out = []
    folder = Path(tempfile.mkdtemp())
    for day, (issue, _, pages, columns) in ISSUES.items():
        pdf = raw.cached(SOURCE, url(issue)).path
        for page in pages:
            image = Image.open(scan.rendered(pdf, page, folder))
            width, height = image.size
            words = scan.layer(pdf, page)
            for column in range(columns):
                middle = gutter(words, width)
                x0, x1 = (0, width) if columns == 1 else ((0, middle) if column == 0 else (middle, width))
                busy = [False] * height
                for _, box in words:
                    if x0 <= (box[0] + box[2]) / 2 < x1:
                        for y in range(max(box[1] - 4, 0), min(box[3] + 4, height)):
                            busy[y] = True
                cut = max(
                    range(int(height * 0.4), int(height * 0.6)), key=lambda y: (not busy[y], -abs(y - height / 2))
                )
                for half, (y0, y1) in enumerate(((0, cut), (cut, height))):
                    crop = image.crop((x0, y0, x1, y1))
                    crop.thumbnail((1568, 1568))
                    buffer = io.BytesIO()
                    crop.save(buffer, "PNG")
                    data = buffer.getvalue()
                    out.append((day, page, column, half, hashlib.sha256(data).hexdigest(), "image/png", data))
    return out


def response(digest: str) -> dict[str, list[dict[str, str]]] | None:
    try:
        return json.loads(raw.cached(SOURCE, f"anthropic-batch:{MODEL}/{PROMPT}/{digest}").path.read_text())
    except LookupError:
        return None


def read() -> None:
    plan = crops()
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "crops.json").write_text(
        json.dumps([[day.isoformat(), page, column, half, digest] for day, page, column, half, digest, *_ in plan])
    )
    state_path = PARSED / "batches.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    api = anthropic.Anthropic()
    for batch_id, keys in list(state.items()):
        status = api.messages.batches.retrieve(batch_id)
        if status.processing_status != "ended":
            print(batch_id, status.processing_status, status.request_counts.processing, "processing")
            continue
        spent = 0.0
        for line in api.get(
            f"/v1/messages/batches/{batch_id}/results",
            cast_to=str,
            options={"headers": {"Accept": "application/binary"}},
        ).splitlines():
            result = MessageBatchIndividualResponse.model_validate_json(line)
            if result.result.type != "succeeded":
                print(result.custom_id, result.result.type)
                continue
            spent += dollars(result.result.message, 1.0)
            raw.store(
                SOURCE,
                keys[result.custom_id],
                result.result.message.model_dump_json().encode(),
                suffix=".json",
                retrieved_at=datetime.now(UTC),
                media_type="application/json",
            )
        books = ledger()
        books["pending"].pop(batch_id, None)
        books["spent"][batch_id] = spent
        LEDGER.write_text(json.dumps(books, indent=2))
        print(batch_id, f"collected ${spent:.3f}")
        del state[batch_id]
    todo = [crop for crop in plan if response(crop[4]) is None]
    print(f"{len(plan) - len(todo)} of {len(plan)} crops read")
    if todo and not state:
        books = ledger()
        projected = len(todo) * 0.002
        committed = sum(books["spent"].values()) + sum(books["pending"].values())
        if committed + projected > STOP:
            raise SystemExit(f"BLOCKED: budget; ${committed:.2f} committed plus ${projected:.2f} passes ${STOP}")
        keys = {}
        requests: list[Request] = []
        for day, page, column, half, digest, media, data in todo:
            custom = f"{day.year}-{page}-{column}-{half}"
            keys[custom] = f"anthropic-batch:{MODEL}/{PROMPT}/{digest}"
            requests.append(
                {
                    "custom_id": custom,
                    "params": {
                        "model": MODEL,
                        "max_tokens": 16000,
                        "system": SYSTEM,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "image/png",
                                            "data": base64.b64encode(data).decode(),
                                        },
                                    },
                                    {"type": "text", "text": "Transcribe this crop."},
                                ],
                            }
                        ],
                        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                    },
                }
            )
        created = api.messages.batches.create(requests=requests)
        books["pending"][created.id] = projected
        books.setdefault("when", {})[created.id] = time.time()
        LEDGER.write_text(json.dumps(books, indent=2))
        state[created.id] = keys
        print("submitted", created.id, len(requests), f"projected ${projected:.2f}")
    state_path.write_text(json.dumps(state, indent=2))


def lines(pdf: Path, page: int, columns: int) -> list[tuple[int, list[tuple[str, tuple[float, ...]]]]]:
    words = scan.layer(pdf, page)
    width = max(box[2] for _, box in words)
    split = gutter(words, int(width))
    out: list[tuple[int, float, list[tuple[str, tuple[float, ...]]]]] = []
    for text, box in sorted(words, key=lambda word: word[1][1]):
        column = 0 if columns == 1 else int((box[0] + box[2]) / 2 > split)
        middle = (box[1] + box[3]) / 2
        for index in range(len(out) - 1, -1, -1):
            at, y, found = out[index]
            if at == column and abs(y - middle) < (box[3] - box[1]) * 0.5:
                found.append((text, box))
                break
            if middle - y > 80:
                out.append((column, middle, [(text, box)]))
                break
        else:
            out.append((column, middle, [(text, box)]))
    out.sort(key=lambda line: (line[0], line[1]))
    return [(column, sorted(found, key=lambda word: word[1][0])) for column, _, found in out]


def number(words: list[tuple[str, tuple[float, ...]]]) -> tuple[int | None, list[float] | None]:
    tail = []
    for text, box in reversed(words):
        if NUMBER.match(text) and any(character.isdigit() for character in text):
            tail.append((text, box))
        else:
            break
    if not tail:
        return None, None
    digits = re.sub(r"\D", "", "".join(text.replace("J", "") for text, _ in reversed(tail)))
    boxes = [box for _, box in tail]
    return int(digits), [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def abbreviation(words: list[str]) -> str | None:
    head = ""
    for text in words:
        if text.lower().startswith(("sinin", "nin", "»", ">", "geçerli", "gecerli", "un", "nun")) or text.startswith(
            ":"
        ):
            break
        head += text
    key = re.sub(r"[^A-ZÇĞİÖŞÜ]", "", head.upper().replace("1", "I").replace("İ", "I").replace("Î", "I"))
    return ABBREVIATIONS.get(key)


def party(text: str, year: int) -> str | None:
    folded = fold(text)
    for word, code in PARTY_WORDS:
        if word in folded:
            return "CGP" if code == "GP" and year > 1972 else "TBP" if code == "BP" and year > 1971 else code
    return None


def heading(text: str) -> str | None:
    tokens = text.split()
    if re.search(r"\d", text) or (len(tokens) > 4 and not all(len(token) <= 2 for token in tokens)):
        return None
    letters = LETTERS.findall(text)
    if len(letters) < 3 or sum(letter.isupper() for letter in letters) < 0.7 * len(letters):
        return None
    folded = fold(text)
    match = folded if folded in KNOWN else next(iter(difflib.get_close_matches(folded, KNOWN, 1, 0.8)), None)
    return None if match is None else ALIASES.get(match, match)


def ocr(day: date) -> list[tuple[int, str, str, int | None, list[float] | None]]:
    issue, _, pages, columns = ISSUES[day]
    pdf = raw.cached(SOURCE, url(issue)).path
    out = []
    split: dict[int, float] = {}
    for page in pages:
        layer = scan.layer(pdf, page)
        width = max(box[2] for _, box in layer)
        height = max(box[3] for _, box in layer)
        for column, found in lines(pdf, page, columns):
            words = [(html.unescape(text), box) for text, box in found]
            folded = fold(" ".join(text for text, _ in words))
            if any(word in folded for word in ("secilenaday", "kazananaday", "siyasiparti", "veyabag")):
                at = next((box[0] for text, box in words if fold(text).startswith("bag")), None)
                if at is not None:
                    split[column] = at
            value, box = number(words)
            cut = split.get(column, width * (0.5 if columns == 1 else 0.25 + 0.5 * column)) - 10
            out.append(
                (
                    page,
                    " ".join(text for text, box in words if box[0] < cut),
                    " ".join(text for text, box in words if box[0] >= cut),
                    value,
                    None if box is None else [box[0] / width, box[1] / height, box[2] / width, box[3] / height],
                )
            )
    return out


def vision(day: date) -> list[tuple[int, str, str, int | None, list[float] | None]]:
    out = []
    for at, page, _, _, digest in json.loads((PARSED / "crops.json").read_text()):
        if at != day.isoformat():
            continue
        reply = response(digest)
        if reply is None:
            raise SystemExit(f"crop {at} {page} not read; run read()")
        text = next(block["text"] for block in reply["content"] if block["type"] == "text")
        for line in json.loads(text)["lines"]:
            right = line["right"].strip()
            value = int(re.sub(r"\D", "", right)) if re.fullmatch(r"[\d.,\s]*\d[\d.,\s]*", right) else None
            out.append((page, line["left"].strip(), right, value, None))
    return out


def walk(day: date, stream: list[tuple[int, str, str, int | None, list[float] | None]]) -> tuple[list, list]:
    values: list[dict[str, object]] = []
    names: list[dict[str, object]] = []
    chamber = "mv" if day.year in (1965, 1969) else None
    province = mode = current = pending = None
    for page, left, right, value, box in stream:
        text = f"{left} {right}".strip()
        folded = fold(text)
        if (
            not folded
            or any(word in folded for word in FURNITURE)
            or re.search(r"\d+(ekim|haziran)19\d\d|sayi\d{5}", folded)
        ):
            continue
        if "senato" in folded or ("cumhuriyet" in folded and "uyeler" in folded):
            chamber, mode = "senate", None
            continue
        if "milletvekilisecimi" in folded and len(folded) < 30:
            chamber, mode = "mv", None
            continue
        found = heading(text)
        if found is not None:
            province, mode, pending, current = found, "counts", None, None
            continue
        if chamber != "mv" or province is None:
            continue
        if "secilenaday" in folded or "kazananaday" in folded:
            mode = "names"
            continue
        if mode == "names" and folded.startswith("secmensayisi"):
            mode = "counts"
        if mode == "names":
            if "adivesoyadi" in folded or "siyasiparti" in folded or "veyabagimsiz" in folded:
                continue
            if any(word in folded for word in STOPS) or sum(len(token) > 1 for token in left.split()) > 7:
                mode = None
                continue
            label = re.sub(
                r"(?:\b\w\b ?){2,}",
                lambda run: re.sub(r"(?<=\w)(?=[A-ZÇĞİÖŞÜ])", " ", run.group().replace(" ", "")) + " ",
                left,
            ).strip()
            label = re.sub(r"^[^A-Za-zÇĞİÖŞÜçğıöşüÂâÎî]+", "", label)
            label = re.sub(r"[^A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû)]+$", "", label)
            tokens = label.split()
            moved = []
            while len(tokens) > 2 and (fold(tokens[-1]) in PARTY_START or re.fullmatch(r"[A-ZÇİ]\.", tokens[-1])):
                moved.insert(0, tokens.pop())
            label = " ".join(tokens)
            right = " ".join([*moved, right]).strip()
            abbreviated = re.search(r"\s+[A-ZÇ]\s*\.\s*'?\s*(Sel[âa]met|Hareket|Halk|Güven|Parti\w*)$", label)
            if abbreviated is not None:
                label = label[: abbreviated.start()]
                right = f"{abbreviated.group().strip()} {right}".strip()
            if (
                len(LETTERS.findall(label)) < 2
                or len(label.split()) < 2
                or any(fold(token) in HEADER for token in label.split())
            ):
                continue
            last = names[-1] if names and names[-1]["province"] == province else None
            if last is not None and (label[0].islower() or str(last["name"]).endswith("-")):
                last["name"] = (
                    f"{last['name']}{label}" if str(last["name"]).endswith("-") else f"{last['name']} {label}"
                )
                continue
            code = party(right, day.year) or current
            current = code
            names.append(
                {
                    "day": day,
                    "province": province,
                    "order": 1 + sum(1 for name in names if name["province"] == province),
                    "name": label,
                    "party": code,
                    "independent": code == "IND" or "bagimsiz" in fold(right),
                    "page": page,
                }
            )
            continue
        if mode != "counts":
            continue
        measure = None
        if pending is not None:
            measure, pending = pending, None
        elif folded.startswith("secmensayisi"):
            measure = "registered"
        elif "oyunukullanan" in folded or "oykullanan" in folded:
            measure = "voters"
        elif "katilma" in folded:
            continue
        elif ("gecerli" in folded[:12] or "cecerli" in folded[:12]) and (
            "sayilan" in folded[:20] or folded.startswith(("gecerlioysayisi", "gecerlioy"))
        ):
            measure = "valid"
        elif "bagimsiz" in folded:
            measure = "independents"
            if value is None:
                pending = "independents"
                continue
        elif (
            (code := abbreviation(text.split())) is not None
            or (code := party(left.split(" sinin")[0].split("nin ")[0], day.year)) is not None
            and value is not None
        ):
            measure = f"party:{code}"
        if measure is None or value is None:
            continue
        values.append({"day": day, "province": province, "measure": measure, "value": value, "page": page, "box": box})
    return values, names


def parse() -> None:
    from ea.sources import thea
    from ea.sources.tr import thea as held

    rows = pl.read_parquet(thea.PARSED / "lower.parquet").filter(
        (pl.col("ctr") == held.TURKEY) & pl.col("yr").is_in([day.year for day in ISSUES])
    )
    reference: Counter[tuple[int, str, object]] = Counter()
    seats: Counter[tuple[int, str, object]] = Counter()
    for row in rows.iter_rows(named=True):
        folded = fold(row["cst_n"])
        province = ALIASES.get(folded, folded)
        code = 6000 if row["pty"] >= 6000 else 39 if row["pty"] == 25 else row["pty"]
        reference[(row["yr"], province, code)] += row["pv1"]
        seats[(row["yr"], province, code)] += row["seat"]
        for measure, column in (("registered", "pev1"), ("voters", "vot1"), ("valid", "vv1")):
            reference[(row["yr"], province, measure)] = row[column]
    final = []
    review = []
    named = []
    balanced: Counter[bool] = Counter()
    for day in ISSUES:
        read = {"ocr": walk(day, ocr(day)), "vision": walk(day, vision(day))}
        found: dict[tuple[str, str], dict[str, list[dict[str, object]]]] = {}
        for reader, (values, _) in read.items():
            for row in values:
                found.setdefault((row["province"], row["measure"]), {}).setdefault(reader, []).append(row)
        for (province, measure), by in sorted(found.items()):
            first = by.get("ocr", [])
            second = by.get("vision", [])
            a = first[0]["value"] if len(first) == 1 else None
            b = second[0]["value"] if len(second) == 1 else None
            code = measure.removeprefix("party:")
            ref = reference.get((day.year, province, CLEA.get(code, code)))
            if (
                day.year in (1969, 1977)
                and CLEA.get(code) == 47
                and any((province, f"party:{other}") in found for other in ("TİP", "BP", "TBP") if other != code)
            ):
                ref = None
            how = None
            value = None
            if a is not None and a == b:
                value, how = a, "agree"
            elif ref is not None and b is not None and b == ref:
                value, how = b, "vision+thea"
            elif ref is not None and a is not None and a == ref:
                value, how = a, "ocr+thea"
            if how is None:
                review.append(
                    {
                        "day": day,
                        "province": province,
                        "measure": measure,
                        "ocr": a,
                        "vision": b,
                        "thea": ref,
                        "page": (second or first)[0]["page"],
                    }
                )
                continue
            box = next((row["box"] for row in first if row["box"] is not None), None)
            final.append(
                {
                    "day": day,
                    "province": province,
                    "measure": measure,
                    "value": value,
                    "how": how,
                    "page": (first or second)[0]["page"] if box is not None else second[0]["page"],
                    "box": box,
                }
            )
        measures = {17: "party:AP", 40: "party:CHP", 21: "party:MP", 22: "party:MSP", 29: "party:YTP", 41: "party:CGP"}
        measures |= {39: "party:MHP" if day.year > 1965 else "party:CKMP", 48: "party:GP", 7: "party:DP"}
        measures[6000] = "independents"
        for province in sorted({where for where, _ in found}):
            got = {
                str(row["measure"]): value
                for row in final
                if row["day"] == day and row["province"] == province and isinstance(value := row["value"], int)
            }
            if "valid" not in got:
                continue
            wanted = {
                measure
                for where, measure in found
                if where == province and (measure.startswith("party:") or measure == "independents")
            } | {
                measures[code]
                for (year, where, code), value in reference.items()
                if year == day.year and where == province and isinstance(code, int) and code in measures and value > 0
            }
            unsettled = sorted(wanted - set(got))
            derived = got["valid"] - sum(got[measure] for measure in wanted & set(got))
            if not unsettled:
                balanced[derived == 0] += 1
                continue
            if len(unsettled) != 1:
                continue
            disputed = next(
                (
                    row
                    for row in review
                    if row["day"] == day and row["province"] == province and row["measure"] == unsettled[0]
                ),
                None,
            )
            code = unsettled[0].removeprefix("party:")
            readings = (
                {disputed["ocr"], disputed["vision"], disputed["thea"]}
                if disputed is not None
                else {reference.get((day.year, province, CLEA.get(code, 6000 if code == "independents" else code)))}
            )
            if derived not in readings:
                continue
            if disputed is not None:
                review.remove(disputed)
            final.append(
                {
                    "day": day,
                    "province": province,
                    "measure": unsettled[0],
                    "value": derived,
                    "how": "sum",
                    "page": disputed["page"]
                    if disputed is not None
                    else next(
                        row["page"]
                        for row in final
                        if row["day"] == day and row["province"] == province and row["measure"] == "valid"
                    ),
                    "box": None,
                }
            )
        ocr_names = Counter(row["province"] for row in read["ocr"][1])
        for province in sorted(
            {row["province"] for row in read["vision"][1]} | {row["province"] for row in read["ocr"][1]}
        ):
            want = sum(value for (year, where, _), value in seats.items() if year == day.year and where == province)
            listed = [row for row in read["vision"][1] if row["province"] == province]
            fallback = [row for row in read["ocr"][1] if row["province"] == province]
            if len(listed) != want and len(fallback) == want:
                listed = fallback
            if (day, province) in DAMAGED:
                listed = [
                    {
                        "day": day,
                        "province": province,
                        "order": order,
                        "name": name,
                        "party": code,
                        "independent": code == "IND",
                        "page": 6,
                    }
                    for order, (name, code) in enumerate(DAMAGED[day, province], 1)
                ]
            if len(listed) != want:
                review.append(
                    {
                        "day": day,
                        "province": province,
                        "measure": "names",
                        "ocr": ocr_names[province],
                        "vision": len(listed),
                        "thea": want,
                        "page": listed[0]["page"] if listed else None,
                    }
                )
            named.extend(listed)
    for day, (issue, *_) in TABLED.items():
        pdf = raw.cached(SOURCE, url(issue)).path
        sizes = {
            int(page): (float(width) * 300 / 72, float(height) * 300 / 72)
            for page, width, height in re.findall(
                r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)",
                subprocess.run(
                    ["pdfinfo", "-f", "12", "-l", "13", str(pdf)], capture_output=True, text=True, check=True
                ).stdout,
            )
        }
        located: dict[str, tuple[int, float, dict[int, list[float]]]] = {}
        for page in (12, 13):
            words = scan.layer(pdf, page)
            for text, box in words:
                folded = "agri" if fold(text) == "agn" else fold(text)
                if box[0] > sizes[page][0] * 0.15 or len(folded) < 3:
                    continue
                match = (
                    folded if folded in KNOWN else next(iter(difflib.get_close_matches(folded, KNOWN, 1, 0.75)), None)
                )
                middle = (box[1] + box[3]) / 2
                line = sorted(
                    (
                        word
                        for word in words
                        if abs((word[1][1] + word[1][3]) / 2 - middle) < 22 and word[1][0] > box[2]
                    ),
                    key=lambda word: word[1][0],
                )
                if (
                    match is None
                    or ALIASES.get(match, match) in located
                    or not line
                    or not re.search(r"\d", line[0][0])
                ):
                    continue
                cells: dict[int, list[float]] = {}
                for _, at in line:
                    held = cells.setdefault(
                        min(range(len(EDGES)), key=lambda index: abs(EDGES[index] - at[2])), list(at)
                    )
                    held[:] = [min(held[0], at[0]), min(held[1], at[1]), max(held[2], at[2]), max(held[3], at[3])]
                located[ALIASES.get(match, match)] = (page, middle, cells)
        columns = {
            "registered": 0,
            "voters": 1,
            "valid": 3,
            "party:AP": 4,
            "party:CHP": 5,
            "party:CKMP": 6,
            "party:YTP": 7,
            "independents": 9,
        }
        for row in pl.read_csv(Path(__file__).with_name("ysk_mv_1961.tsv"), separator="\t").iter_rows(named=True):
            province = fold(PROVINCES[row["plate"] - 1])
            page, middle, cells = located[province]
            width, height = sizes[page]
            for measure, column in columns.items():
                value = row[measure.removeprefix("party:")]
                if value == 0 and measure not in TOTALS:
                    continue
                box = cells.get(
                    column, [EDGES[column - 1] + 10 if column else 560, middle - 20, EDGES[column], middle + 20]
                )
                final.append(
                    {
                        "day": day,
                        "province": province,
                        "measure": measure,
                        "value": value,
                        "how": "printed",
                        "page": page,
                        "box": [box[0] / width, box[1] / height, box[2] / width, box[3] / height],
                    }
                )
        for order, row in enumerate(
            pl.read_csv(Path(__file__).with_name("ysk_mv_1961_members.tsv"), separator="\t", quote_char=None).iter_rows(
                named=True
            ),
            1,
        ):
            named.append(
                {
                    "day": day,
                    "province": fold(PROVINCES[row["plate"] - 1]),
                    "order": order,
                    "name": row["name"],
                    "party": row["party"],
                    "independent": row["independent"],
                    "page": row["page"],
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(final).write_parquet(PARSED / "values.parquet")
    pl.DataFrame(named).write_parquet(PARSED / "names.parquet")
    pl.DataFrame(review, infer_schema_length=None).write_parquet(PARSED / "review.parquet")
    print("balanced", balanced)
    print(pl.DataFrame(final).group_by(pl.col("day").dt.year(), "how").len().sort("day", "how"))
    print(pl.DataFrame(named).group_by(pl.col("day").dt.year()).len().sort("day"))
    print(pl.DataFrame(review, infer_schema_length=None).group_by(pl.col("day").dt.year(), "measure").len())


def emit() -> Path:
    values = pl.read_parquet(PARSED / "values.parquet")
    names = pl.read_parquet(PARSED / "names.parquet")
    plates = {fold(name): plate for plate, name in enumerate(PROVINCES, 1)}
    with connect() as conn:
        members: dict[str, list[tuple[UUID, str]]] = {}
        for key, person, text in conn.execute(
            "select x.value, t.person_id, n.text from tenure t"
            " join external_id x on x.entity_id = t.contest_id and x.scheme = 'tr.contest'"
            " join name n on n.entity_id = t.person_id and n.is_preferred"
            " where x.value like any(%s) and t.acquired_via = 'elected'",
            [[f"{day}:parliament:%" for day in ISSUES | TABLED]],
        ):
            members.setdefault(key, []).append((person, text))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Official National Assembly results by province for the 1961, 1965, 1969, 1973, and 1977 Turkish "
                "general elections, with the elected members, from YSK's notices in the Resmî Gazete"
            ),
            polity="TR",
        )
        cells = []
        unmatched = []
        doubtful = []
        moved = []
        listed_candidacies: set[UUID] = set()
        PAGES.mkdir(parents=True, exist_ok=True)
        for day, (issue, published, _, _) in (ISSUES | TABLED).items():
            held = values.filter(pl.col("day") == day)
            listed = names.filter(pl.col("day") == day)
            documents = [
                (
                    issue,
                    f"{day.year} milletvekili genel seçimi sonuçları, Resmî Gazete {issue} ({published:%d.%m.%Y})",
                    NOTES_1961 if day in TABLED else NOTES,
                    sorted(set(held["page"]) | set(listed["page"])),
                )
            ]
            if day == REVISION_DAY:
                documents.append((REVISION_ISSUE, REVISION_TITLE, REVISION_NOTES, [3, 4]))
            sources = {}
            page_ids = {}
            for printed_in, title, notes, numbers in documents:
                url = f"{ARCHIVE}/{printed_in}.pdf"
                sources[printed_in] = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=sources[printed_in],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=url,
                    license=LICENSE,
                    notes=notes,
                )
                pdf = raw.cached(SOURCE, url)
                artifact = cs.artifact(pdf, sources[printed_in])
                for number in numbers:
                    image = PAGES / f"{pdf.sha256}-{number}.png"
                    if not image.exists():
                        subprocess.run(
                            [
                                "pdftoppm",
                                "-r",
                                "100",
                                "-png",
                                "-f",
                                str(number),
                                "-l",
                                str(number),
                                "-singlefile",
                                str(pdf.path),
                                str(image.with_suffix("")),
                            ],
                            check=True,
                        )
                    width, height = struct.unpack(">II", image.read_bytes()[16:24])
                    page_ids[printed_in, number] = cs.page(artifact, number)
                    cs.add(
                        "source_page",
                        id=page_ids[printed_in, number],
                        artifact_id=artifact,
                        page_number=number,
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
            source = sources[issue]
            everyone = [member for key, group in members.items() if key.startswith(f"{day}:") for member in group]
            election = cs.key("tr.election", str(day))
            for province in sorted(set(held["province"])):
                plate = plates[province]
                contest_key = f"{day}:parliament:{plate:02}"
                contest = cs.key("tr.contest", contest_key)
                unit = cs.unit("tr.il", f"{plate:02}")
                if {election, contest, unit} & cs.minted:
                    raise ValueError("publish tr.thea before tr.ysk_mv")
                declared = cs.key(f"{SOURCE}.result_set", contest_key)
                counted = dict(held.filter(pl.col("province") == province).select("measure", "value").iter_rows())
                cs.add(
                    "result_set",
                    id=declared,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=None
                    if "valid" not in counted
                    else min(
                        1,
                        round(
                            sum(
                                value
                                for measure, value in counted.items()
                                if measure.startswith("party:") or measure == "independents"
                            )
                            / counted["valid"],
                            4,
                        ),
                    ),
                )
                options: dict[str, tuple[UUID, UUID | None]] = {}

                def option(
                    code: str,
                    options: dict[str, tuple[UUID, UUID | None]] = options,
                    day: date = day,
                    contest_key: str = contest_key,
                    contest: UUID = contest,
                ) -> tuple[UUID, UUID | None]:
                    if code not in options:
                        key = (
                            "turkiye-isci-partisi-1975" if code == "TİP" and day.year == 1977 else PARTY_KEYS.get(code)
                        )
                        label = (
                            INDEPENDENTS
                            if key is None
                            else next(
                                (
                                    full
                                    for full, _, start, end in PARTIES.get(key, [])
                                    if (start is None or start <= day) and (end is None or day < end)
                                ),
                                "Türkiye İşçi Partisi",
                            )
                        )
                        party = None if key is None else cs.key("tr.party", key)
                        id = cs.key("tr.option", f"{contest_key}:{slug(label)}")
                        if id in cs.minted:
                            cs.add(
                                "contest_option",
                                id=id,
                                contest_id=contest,
                                kind="independents" if key is None else "list",
                                party_id=party,
                                ballot_label=label,
                            )
                        options[code] = (id, party)
                    return options[code]

                for measure, value, page, box in (
                    held.filter(pl.col("province") == province).select("measure", "value", "page", "box").iter_rows()
                ):
                    cells.append(
                        (
                            declared,
                            unit,
                            None
                            if measure in TOTALS
                            else option("IND" if measure == "independents" else measure.removeprefix("party:"))[0],
                            TOTALS.get(measure, VOTES),
                            value,
                            page_ids[issue, page],
                            box if box is not None else [0.0, 0.0, 1.0, 1.0],
                        )
                    )
                rosters = [
                    (
                        declared,
                        source,
                        [
                            (printed, code, page_ids[issue, page], independent)
                            for printed, code, page, independent in listed.filter(pl.col("province") == province)
                            .sort("order")
                            .select("name", "party", "page", "independent")
                            .iter_rows()
                        ],
                    )
                ]
                if day == REVISION_DAY and plate in REVISED:
                    revised = cs.key(f"{SOURCE}.result_set", f"{contest_key}:revised")
                    cs.add(
                        "result_set",
                        id=revised,
                        election_id=election,
                        contest_id=contest,
                        stage="court_revised",
                        kind="counted",
                        status="active",
                        source_id=sources[REVISION_ISSUE],
                        is_default=False,
                        completeness=0,
                    )
                    cs.add("note", entity_id=revised, kind="context", body_md=REVISION_NOTE)
                    roster = list(rosters[0][2])
                    for removed, added, code, page in REVISED[plate]:
                        roster.pop(
                            max(
                                (difflib.SequenceMatcher(None, fold(entry[0]), fold(removed)).ratio(), index)
                                for index, entry in enumerate(roster)
                            )[1]
                        )
                        roster.append((added, code, page_ids[REVISION_ISSUE, page], False))
                    rosters.append((revised, sources[REVISION_ISSUE], roster))
                pool = members.get(contest_key, [])
                for result_set, cited, roster in rosters:
                    pairs = sorted(
                        (
                            (
                                0.95
                                if (day, province) in DAMAGED and fold(known).endswith(fold(printed))
                                else max(
                                    difflib.SequenceMatcher(None, fold(printed), fold(known)).ratio(),
                                    difflib.SequenceMatcher(
                                        None,
                                        " ".join(sorted(fold(token) for token in re.split(r"[\s()-]+", printed))),
                                        " ".join(sorted(fold(token) for token in re.split(r"[\s()-]+", known))),
                                    ).ratio(),
                                ),
                                index,
                                person,
                                known,
                            )
                            for index, (printed, *_) in enumerate(roster)
                            for person, known in pool
                        ),
                        key=lambda pair: -pair[0],
                    )
                    matched: dict[int, UUID] = {}
                    printed_as: dict[int, str] = {}
                    taken = set()
                    for score, index, person, known in pairs:
                        if score < 0.6 or index in matched or person in taken:
                            continue
                        matched[index] = person
                        if (day, province) in DAMAGED:
                            printed_as[index] = known
                        taken.add(person)
                        if score < 0.8:
                            doubtful.append((contest_key, roster[index][0], known, round(score, 2)))
                    for index, (printed, *_) in enumerate(roster):
                        if index in matched:
                            continue
                        score, person, known = max(
                            (
                                max(
                                    difflib.SequenceMatcher(None, fold(printed), fold(known)).ratio(),
                                    difflib.SequenceMatcher(
                                        None,
                                        " ".join(sorted(fold(token) for token in re.split(r"[\s()-]+", printed))),
                                        " ".join(sorted(fold(token) for token in re.split(r"[\s()-]+", known))),
                                    ).ratio(),
                                ),
                                person,
                                known,
                            )
                            for person, known in everyone
                        )
                        if score >= 0.85:
                            matched[index] = person
                            moved.append((contest_key, printed, known))
                    left = [index for index in range(len(roster)) if index not in matched]
                    free = [(person, known) for person, known in pool if person not in taken]
                    if len(left) == 1 and len(free) == 1 and not (day == REVISION_DAY and plate in REVISED):
                        matched[left[0]] = free[0][0]
                        doubtful.append((contest_key, roster[left[0]][0], free[0][1], "last"))
                    seats: Counter[str] = Counter()
                    for index, (printed, code, page_id, independent) in enumerate(roster):
                        if code is None:
                            raise ValueError(f"{contest_key} {printed} has no party")
                        printed = printed_as.get(index, printed)
                        seats[code] += 1
                        person = matched.get(index)
                        if person is None:
                            person = cs.key(f"{SOURCE}.person", f"{contest_key}:{slug(printed)}")
                            unmatched.append((contest_key, printed))
                            if person in cs.minted:
                                cs.add("person", id=person)
                                cs.add(
                                    "name",
                                    entity_id=person,
                                    text=printed,
                                    lang="tr",
                                    kind="official",
                                    is_preferred=True,
                                    valid_from=None,
                                    valid_from_precision="day",
                                    valid_to=None,
                                    valid_to_precision="day",
                                )
                        option_id, party = option(code)
                        candidacy = cs.key("tr.candidacy", f"{contest_key}:{slug(printed)}")
                        if candidacy not in listed_candidacies:
                            listed_candidacies.add(candidacy)
                            cs.add(
                                "candidacy",
                                id=candidacy,
                                person_id=person,
                                option_id=option_id,
                                role="list_member",
                                list_position=None,
                                ballot_name=printed,
                                member_of_party_id=None if independent else party,
                                is_independent=independent,
                            )
                            if party is not None:
                                cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party, kind="nominated")
                            cs.add(
                                "citation",
                                entity_id=candidacy,
                                source_id=cited,
                                locator={"page_id": str(page_id)},
                            )
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option_id,
                            candidacy_id=candidacy,
                            kind="elected",
                        )
                    for code, won in sorted(seats.items()):
                        cs.add("outcome", result_set_id=result_set, option_id=option(code)[0], kind="won", seats=won)
        located = pl.DataFrame(
            [
                (str(result_set), str(unit), None if option is None else str(option), measure, value, str(page), box)
                for result_set, unit, option, measure, value, page, box in cells
            ],
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "page_id": pl.String,
                "box": pl.List(pl.Float64),
            },
            orient="row",
        )
        keys = {
            "result_set_id": "result_set_id",
            "unit_id": "unit_id",
            "option_id": "option_id",
            "channel": pl.lit(0, pl.Int16),
            "measure": "measure",
            "count_round": pl.lit(0, pl.Int16),
        }
        cs.frame("result_value", located.select(**keys, flags=pl.lit(DIGITIZED, pl.Int16), value="value"))
        cs.frame(
            "value_locator",
            located.select(
                **keys,
                page_id="page_id",
                source_id=pl.lit(None, pl.String),
                x0=pl.col("box").list.get(0),
                y0=pl.col("box").list.get(1),
                x1=pl.col("box").list.get(2),
                y1=pl.col("box").list.get(3),
            ),
        )
        print("unmatched", len(unmatched), unmatched[:40])
        print("doubtful", len(doubtful), doubtful[:60])
        print("moved", len(moved), moved)
        return cs.write()
