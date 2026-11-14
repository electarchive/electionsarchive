import base64
import csv
import difflib
import io
import json
import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from statistics import median
from uuid import UUID

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.sources.id.kpu_pilpres import PROVINCES
from ea.store import DATA, connect

SOURCE = "id.kpu_dpd_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KPU = "Komisi Pemilihan Umum Republik Indonesia"
JDIH = "https://jdih.kpu.go.id/data/data_kepkpu/"
DAY = date(2014, 4, 9)
RESULTS = (
    f"{JDIH}952014_Penetapan_Hasil_Pileg.pdf",
    (
        "Keputusan KPU Nomor 411/Kpts/KPU/Tahun 2014 tentang Penetapan Hasil Pemilihan Umum Anggota DPR, DPD, DPRD "
        "Provinsi, dan DPRD Kabupaten/Kota Secara Nasional dalam Pemilihan Umum Tahun 2014: Lampiran Model E-1 DPD, "
        "Jumlah Perolehan Suara Sah dan Peringkat Suara Sah Calon Anggota DPD"
    ),
)
ELECTED = (
    f"{JDIH}SK_KPU_417_Penetapan_calon_terpilih_DPD_1452014.pdf",
    (
        "Keputusan KPU Nomor 417/Kpts/KPU/Tahun 2014 tentang Penetapan Calon Terpilih Anggota Dewan Perwakilan Daerah "
        "dalam Pemilihan Umum Tahun 2014: Daftar Calon Terpilih (Model E-3 DPD)"
    ),
)
FIRST_PAGE = 8
LAST_PAGE = 55
ALIASES = {"BANGKA BELITUNG": "KEPULAUAN BANGKA BELITUNG", "DI YOGYAKARTA": "DAERAH ISTIMEWA YOGYAKARTA"}
MODEL = "claude-haiku-5-5"
SYSTEM = (
    "You transcribe scanned Indonesian election certificates (Model E-1 DPD) exactly. Copy every candidate row of "
    "the table in order. Numbers use a period as the thousands separator; copy them digit for digit without "
    "separators. Never compute or guess a figure."
)
TEXT = (
    "Transcribe this page: `province` is the DAERAH PEMILIHAN named in the heading, `part` is the Halaman number "
    "printed top right (empty if none), `rows` are the candidate rows with the printed row number, the candidate name, "
    "the SUARA SAH figure, and the PERINGKAT figure (empty when not printed), and `total_label` and `total` are the "
    "last row of the table (JUMLAH PINDAHAN, JUMLAH, or similar) and its figure (empty when there is none)."
)
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "province": {"type": "string"},
        "part": {"type": "string"},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "name": {"type": "string"},
                    "votes": {"type": "string"},
                    "rank": {"type": "string"},
                },
                "required": ["number", "name", "votes", "rank"],
                "additionalProperties": False,
            },
        },
        "total_label": {"type": "string"},
        "total": {"type": "string"},
    },
    "required": ["province", "part", "rows", "total_label", "total"],
    "additionalProperties": False,
}


def fetch() -> None:
    for url, _ in (RESULTS, ELECTED):
        raw.fetch(SOURCE, url, suffix=".pdf")
    transcribe()


def _pages() -> list[Path]:
    folder = PARSED / "scans"
    if not folder.exists():
        folder.mkdir(parents=True)
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "300",
                "-gray",
                "-png",
                "-f",
                str(FIRST_PAGE),
                "-l",
                str(LAST_PAGE),
                str(raw.cached(SOURCE, RESULTS[0]).path),
                str(folder / "p"),
            ],
            check=True,
        )
    return sorted(folder.glob("p-*.png"))


def transcribe() -> None:
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for page in _pages():
        image = Image.open(page)
        image.thumbnail((2000, 2000))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        wanted[page.stem] = (
            MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=8000,
                system=SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            ImageBlockParam(
                                type="image",
                                source={
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64.b64encode(buffer.getvalue()).decode(),
                                },
                            ),
                            TextBlockParam(type="text", text=TEXT),
                        ],
                    }
                ],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            ),
            (image.width * image.height / 750 * price_in + 4000 * price_out) / 1e6,
        )
    print(batch.ensure(SOURCE, "scans", wanted)[1])


def _number(text: object) -> int | None:
    digits = re.sub(r"\D", "", str(text or ""))
    return int(digits) if digits else None


def _read(stem: str) -> dict[str, object]:
    message = json.loads(raw.cached(SOURCE, stem).path.read_text())
    return json.loads("".join(block["text"] for block in message["content"] if block["type"] == "text"))


def _figures(read: dict[str, object]) -> list[tuple[object, object]]:
    rows = read["rows"]
    assert isinstance(rows, list)
    return [(row["number"], row["votes"]) for row in rows] + [("total", read["total"])]


def _elected() -> dict[str, list[tuple[str, int, int]]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, ELECTED[0]).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found: dict[str, list[tuple[str, int, int]]] = {}
    for line in text.splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) != 6 or not parts[0].isdigit() or not parts[5].isdigit():
            continue
        province = ALIASES.get(parts[1], parts[1])
        found.setdefault(province, []).append((parts[3], int(parts[4].replace(".", "")), int(parts[5])))
    return found


def _words(page: Path) -> list[dict[str, str]]:
    out = subprocess.run(
        ["tesseract", str(page), "-", "--psm", "6", "tsv"], check=True, capture_output=True, text=True
    ).stdout
    return [
        word
        for word in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
        if word["level"] == "5" and word["text"].strip()
    ]


def _boxes(page: Path, votes: list[int], total: int | None) -> list[list[float] | None]:
    image = Image.open(page)
    width, height = image.size
    words = _words(page)
    column = [
        word for word in words if word["text"].replace(".", "").replace(",", "").isdigit() and len(word["text"]) >= 3
    ]
    found: dict[int, tuple[float, float, float, float]] = {}
    for index, value in enumerate(votes):
        hits = [word for word in column if _number(word["text"]) == value and word["text"].count(".") <= 2]
        hits = [word for word in hits if float(word["left"]) > width * 0.6]
        if len(hits) == 1:
            word = hits[0]
            left, top = float(word["left"]), float(word["top"])
            found[index] = (left, top, left + float(word["width"]), top + float(word["height"]))
    if len(found) < 5:
        return [None] * (len(votes) + 1)
    slope = median((found[b][1] - found[a][1]) / (b - a) for a in found for b in found if b > a and (b - a) <= 8)
    offset = median(top - slope * index for index, (_, top, _, _) in found.items())
    left = median(box[0] for box in found.values())
    right = median(box[2] for box in found.values())
    thick = median(box[3] - box[1] for box in found.values())
    out: list[list[float] | None] = []
    for index in range(len(votes) + 1):
        if index in found:
            box = found[index]
        else:
            top = offset + slope * index
            box = (left, top, right, top + thick)
        out.append([box[0] / width, box[1] / height, box[2] / width, box[3] / height])
    if total is not None:
        hits = [word for word in column if _number(word["text"]) == total and float(word["left"]) > width * 0.6]
        if len(hits) == 1:
            word = hits[0]
            out[-1] = [
                float(word["left"]) / width,
                float(word["top"]) / height,
                (float(word["left"]) + float(word["width"])) / width,
                (float(word["top"]) + float(word["height"])) / height,
            ]
        else:
            out[-1] = None
    return out


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    elected = _elected()
    names = list(elected)
    if len(names) != 33 or any(len(rows) != 4 for rows in elected.values()):
        raise ValueError(f"decree 417 lists {len(names)} provinces")
    pages: dict[str, list[tuple[Path, dict[str, object]]]] = {}
    for page in _pages():
        read = _read(page.stem)
        label = re.sub(r"\s*DPD\s*$", "", str(read["province"]).upper()).strip()
        label = ALIASES.get(label, label)
        province = [label] if label in names else difflib.get_close_matches(label, names, n=1, cutoff=0.6)
        if not province:
            raise ValueError(f"{page.stem}: no province for {label}")
        if pages.get(province[0]) and _figures(pages[province[0]][-1][1]) == _figures(read):
            continue
        pages.setdefault(province[0], []).append((page, read))
    if set(pages) != set(names):
        raise ValueError(f"missing provinces {set(names) - set(pages)}")
    candidates: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    for province in names:
        rows: list[tuple[Path, int, str, int | None, int | None]] = []
        carried = 0
        grand: int | None = None
        for part, (page, read) in enumerate(pages[province]):
            listed = read["rows"]
            assert isinstance(listed, list)
            for row in listed:
                number, votes = _number(row["number"]), _number(row["votes"])
                if not str(row["name"]).strip():
                    if votes is not None:
                        raise ValueError(f"{page.stem}: unnamed row with votes {row}")
                    continue
                if number is None:
                    raise ValueError(f"{page.stem}: unreadable row {row}")
                rows.append((page, number, str(row["name"]).strip(), votes, _number(row["rank"])))
            total = _number(read["total"])
            if part == 0 and len(pages[province]) > 1:
                carried = total or 0
                if carried != sum(row[3] or 0 for row in rows):
                    raise ValueError(
                        f"{province}: page 1 carries {carried}, rows sum {sum(row[3] or 0 for row in rows)}"
                    )
            else:
                grand = total
        if [row[1] for row in rows] != sorted({row[1] for row in rows}):
            raise ValueError(f"{province}: candidate numbers {[row[1] for row in rows]}")
        summed = sum(row[3] or 0 for row in rows)
        if grand != summed:
            raise ValueError(f"{province}: printed total {grand}, rows sum {summed}")
        ranked = sorted((row for row in rows if row[3] is not None), key=lambda row: -(row[3] or 0))
        if len({row[3] for row in ranked[:5]}) != 5:
            raise ValueError(f"{province}: tie near fourth place")
        top = ranked[:4]
        if [row[3] for row in top] != sorted((entry[1] for entry in elected[province]), reverse=True):
            raise ValueError(f"{province}: top four {[row[3] for row in top]} differ from decree 417")
        for rank, row in enumerate(ranked, 1):
            if row[4] is not None and row[4] != rank:
                raise ValueError(f"{province}: {row[2]} printed rank {row[4]}, computed {rank}")
        declared = {votes: name for name, votes, _ in elected[province]}
        by_page: dict[Path, list[tuple[int, int]]] = {}
        for index, row in enumerate(rows):
            if row[3] is not None:
                by_page.setdefault(row[0], []).append((index, row[3]))
        located: dict[int, list[float] | None] = {}
        for page, entries in by_page.items():
            read = next(read for found, read in pages[province] if found == page)
            total = _number(read["total"]) if page == pages[province][-1][0] else None
            boxes = _boxes(page, [votes for _, votes in entries], total)
            for position, (index, _) in enumerate(entries):
                located[index] = boxes[position]
            if page == pages[province][-1][0]:
                located[len(rows)] = boxes[-1]
        for index, (page, number, name, votes, _) in enumerate(rows):
            is_elected = votes is not None and votes in {row[3] for row in top}
            candidates.append(
                {
                    "province": province,
                    "number": number,
                    "name": declared[votes] if is_elected and votes is not None else name,
                    "votes": votes,
                    "elected": is_elected,
                }
            )
            if votes is None:
                continue
            results.append(
                {
                    "province": province,
                    "measure": "votes",
                    "candidate": number,
                    "value": votes,
                    "page": FIRST_PAGE + int(page.stem.split("-")[1]) - 1,
                    "box": located.get(index),
                }
            )
        last = pages[province][-1][0]
        results.append(
            {
                "province": province,
                "measure": "valid",
                "candidate": None,
                "value": summed,
                "page": FIRST_PAGE + int(last.stem.split("-")[1]) - 1,
                "box": located.get(len(rows)),
            }
        )
    frame = pl.DataFrame(results, infer_schema_length=None)
    boxed = frame.filter(pl.col("box").is_not_null()).height
    frame.write_parquet(PARSED / "results.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "candidates.parquet")
    print(f"{len(results)} results rows ({boxed} with a box), {len(candidates)} candidates")


SYSTEM_NOTE = (
    "Each province elects four members. Voters mark one candidate, and the four candidates with the most valid votes "
    "in the province are elected (UU 8/2012 art. 215 and 216)."
)
NOTE = (
    "Candidate votes and the valid total per province from Model E-1 DPD in the lampiran of KPU decree 411/2014 "
    "(certified 9 May 2014), read from the scanned pages. Candidate votes sum to the printed total, the printed "
    "rankings follow the votes, and the four top candidates and their votes equal the Model E-3 DPD list of decree "
    "417/2014 (14 May 2014). The 2014 election had 33 provinces: North Kalimantan, created in 2012, voted within "
    "East Kalimantan. The decree gives provincial totals only."
)
MEASURE_IDS = {"votes": 1, "valid": 4}


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's Regional Representative Council (DPD) election of 2014: candidates, votes, and the four "
                "elected members in each of 33 provinces, from KPU decrees 411 and 417 of 2014"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        body = cs.key("id.body", "dpd")
        system = cs.key("id.electoral_system", "dpd:sntv-4")
        if system in cs.minted:
            cs.add("electoral_system", id=system, polity_id=indonesia, rules={"formula": "sntv"}, rules_version=1)
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)
        election = cs.key("id.election", DAY.isoformat())

        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        for url, title in (RESULTS, ELECTED):
            sources[url] = cs.source(KPU, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=KPU,
                title=title,
                url=url,
                license="Public record: official decree, no license stated",
                license_url="https://jdih.kpu.go.id",
            )
            artifacts[url] = cs.artifact(raw.cached(SOURCE, url), sources[url])

        pages: dict[int, tuple[UUID, int, int]] = {}

        def page(number: int) -> tuple[UUID, int, int]:
            if number not in pages:
                cached = raw.cached(SOURCE, RESULTS[0])
                image = DATA / "pages" / SOURCE / f"{cached.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(cached.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                found = cs.page(artifacts[RESULTS[0]], number)
                cs.add(
                    "source_page",
                    id=found,
                    artifact_id=artifacts[RESULTS[0]],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
                pages[number] = (found, width, height)
            return pages[number]

        units: dict[str, UUID] = {}

        def unit(code: str) -> UUID:
            if code not in units:
                found = units[code] = cs.unit("id.provinsi", code)
                if found in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=found,
                        polity_id=indonesia,
                        scheme_code="id.provinsi",
                        code=code,
                        area_relation="equals",
                        area_id=cs.area("id.provinsi", code),
                        kind="standard",
                    )
            return units[code]

        day = DAY.isoformat()
        for province in sorted(set(candidates["province"])):
            code = PROVINCES[province]
            key = f"{day}:dpd:{code}"
            contest = cs.key("id.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=cs.area("id.provinsi", code),
                term="full",
                magnitude=4,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            label = province.title().replace("Dki", "DKI").replace("Di ", "DI ")
            name(contest, f"2014 Regional Representative Council election in {label}", "en", "english")
            name(contest, f"Pemilihan Umum Anggota DPD Tahun 2014 Provinsi {label}")
            cs.add("slug", path=f"id/{day}-legislative/dpd/{slug(label)}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[RESULTS[0]],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            options: dict[int, UUID] = {}
            for number, ballot, elected in (
                candidates.filter(pl.col("province") == province).select("number", "name", "elected").rows()
            ):
                option = options[number] = cs.key("id.option", f"{key}:{number}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_position=number,
                    ballot_label=ballot,
                )
                person = cs.key(f"{SOURCE}.person", f"{key}:{number}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, ballot)
                cs.add(
                    "candidacy",
                    id=cs.key("id.candidacy", f"{key}:{number}"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_independent=True,
                )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="elected" if elected else "lost",
                    seats=1 if elected else None,
                )
            for measure, number, value, number_, box in (
                results.filter(pl.col("province") == province)
                .select("measure", "candidate", "value", "page", "box")
                .rows()
            ):
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit(code),
                    "option_id": None if number is None else options[number],
                    "channel": 0,
                    "measure": MEASURE_IDS[measure],
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=value)
                if box is None:
                    continue
                found, _, _ = page(number_)
                x0, y0, x1, y1 = box
                cs.add("value_locator", **fact, page_id=found, source_id=None, x0=x0, y0=y0, x1=x1, y1=y1)
        return cs.write()
