import csv
import io
import re
import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.sources.id.kpu_dpr import _sainte_lague, _slug
from ea.sources.id.kpu_pilpres import DAYS, PROVINCES
from ea.store import DATA

SOURCE = "id.kpu_dpr_2024"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KPU = "Komisi Pemilihan Umum Republik Indonesia"
JDIH = "https://jdih.kpu.go.id/data/data_kepkpu/"
RESULTS = (
    "Keputusan KPU Nomor 360 Tahun 2024 tentang Penetapan Hasil Pemilihan Umum Presiden dan Wakil Presiden, Anggota "
    "DPR, DPD, DPRD Provinsi, dan DPRD Kabupaten/Kota Secara Nasional dalam Pemilihan Umum Tahun 2024"
)
DECREES = {
    "360": (
        f"{JDIH}2024kpt360_L3.pdf",
        f"Lampiran III {RESULTS}: Perolehan Suara Sah dan Peringkat Suara Sah Calon Anggota DPR",
    ),
    "1050": (
        f"{JDIH}2024kpt1050_L3.pdf",
        f"Lampiran III Keputusan KPU Nomor 1050 Tahun 2024 tentang Perubahan atas {RESULTS}",
    ),
    "1060": (
        f"{JDIH}2024kpt1060.pdf",
        f"Keputusan KPU Nomor 1060 Tahun 2024 tentang Perubahan Kedua atas {RESULTS}",
    ),
    "1562": (
        f"{JDIH}2023kpt1562.pdf",
        (
            "Keputusan KPU Nomor 1562 Tahun 2023 tentang Daftar Calon Tetap Anggota Dewan Perwakilan Rakyat dalam "
            "Pemilihan Umum Tahun 2024"
        ),
    ),
    "1205": (
        f"{JDIH}2024kpt1205.pdf",
        (
            "Keputusan KPU Nomor 1205 Tahun 2024 tentang Penetapan Perolehan Kursi Partai Politik Peserta Pemilihan "
            "Umum Anggota Dewan Perwakilan Rakyat dalam Pemilihan Umum Tahun 2024"
        ),
    ),
    "1206": (
        f"{JDIH}2024kpt1206.pdf",
        (
            "Keputusan KPU Nomor 1206 Tahun 2024 tentang Penetapan Calon Terpilih Anggota Dewan Perwakilan Rakyat "
            "dalam Pemilihan Umum Tahun 2024"
        ),
    ),
    **{
        number: (
            f"{JDIH}2024kpt{number}.pdf",
            (
                f"Keputusan KPU Nomor {number} Tahun 2024 tentang Perubahan {order} atas Keputusan KPU Nomor 1206 "
                "Tahun 2024 tentang Penetapan Calon Terpilih Anggota Dewan Perwakilan Rakyat dalam Pemilihan Umum "
                "Tahun 2024"
            ),
        )
        for number, order in (
            ("1208", "Pertama"),
            ("1309", "Kedua"),
            ("1349", "Ketiga"),
            ("1368", "Keempat"),
            ("1401", "Kelima"),
        )
    },
}
AMENDMENTS = ("1208", "1309", "1349", "1368", "1401")
DCT_PARTY = re.compile(r"^\s+(\d{1,2})\s{3,}\S")
DCT_ROW = re.compile(r"^\s{0,10}(\d{1,2})\s+(?:\S.*?\s{2,})?([LP])(?:\s{2,}\S.*|\s*)$")
ALIASES = {"KALIMANTAN TMUR": "KALIMANTAN TIMUR"}
VOTES = re.compile(r"\d{1,3}(?:[.,]\d{3})*")
ROMAN = re.compile(r"[IVXL]+\*?")
THRESHOLD = 4
SEATS = 580
DAPILS = 84


def fetch() -> None:
    for url, _ in DECREES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def _lines(url: str) -> list[list[dict[str, str]]]:
    out = subprocess.run(
        ["pdftotext", "-tsv", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    words = [
        word
        for word in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
        if word["level"] == "5" and word["text"].strip()
    ]
    lines: list[list[dict[str, str]]] = []
    for word in sorted(words, key=lambda word: (int(word["page_num"]), float(word["top"]))):
        first = lines[-1][0] if lines else None
        if first and first["page_num"] == word["page_num"] and float(word["top"]) - float(first["top"]) < 4:
            lines[-1].append(word)
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda word: float(word["left"]))
    return lines


def _box(word: dict[str, str]) -> list[float]:
    left, top, width, height = (float(word[key]) for key in ("left", "top", "width", "height"))
    return [left, top, left + width, top + height]


def _int(text: str) -> int:
    return int(text.replace(".", "").replace(",", ""))


def _after(texts: list[str], label: str) -> str | None:
    if label in " ".join(texts) and ":" in texts:
        found = " ".join(texts[texts.index(":") + 1 :]).strip()
        return re.sub(r"\b(\w) (?=\w\b)", r"\1", found) if re.fullmatch(r"(\w )+\w", found) else found
    return None


def _votes(key: str) -> list[dict[str, object]]:
    url = DECREES[key][0]
    rows: list[dict[str, object]] = []
    province = dapil = None
    party: int | None = None
    for words in _lines(url):
        while len(words) > 2 and words[-1]["text"] in ("PERINGKAT", "SUARA", "SAH"):
            words.pop()
        texts = [word["text"] for word in words]
        page = int(words[0]["page_num"])
        if (found := _after(texts, "PROVINSI")) is not None:
            province = ALIASES.get(found, found)
            continue
        if (found := _after(texts, "DAERAH PEMILIHAN")) is not None:
            dapil = found
            continue
        if dapil is None or all(text.isdigit() for text in texts) or not re.fullmatch(r"\d{1,2}\.?", texts[0]):
            if texts[:2] == ["TOTAL", "SUARA"] and party is not None:
                rows.append(
                    {
                        "dapil": dapil,
                        "province": province,
                        "party": party,
                        "candidate": None,
                        "measure": "total",
                        "votes": _int(texts[-1]),
                        "name": None,
                        "rank": None,
                        "page": page,
                        "box": _box(words[-1]),
                        "decree": key,
                    }
                )
                party = None
            continue
        number = int(texts[0].rstrip("."))
        if texts[1].upper() == "PARTAI" and VOTES.fullmatch(texts[-1]):
            party = number
            rows.append(
                {
                    "dapil": dapil,
                    "province": province,
                    "party": party,
                    "candidate": None,
                    "measure": "party",
                    "votes": _int(texts[-1]),
                    "name": " ".join(texts[1:-1]),
                    "rank": None,
                    "page": page,
                    "box": _box(words[-1]),
                    "decree": key,
                }
            )
            continue
        rank = texts.pop() if ROMAN.fullmatch(texts[-1]) else None
        if party is None or len(texts) < 3 or not VOTES.fullmatch(texts[-1]):
            raise ValueError(f"{key} page {page}: unread line {texts}")
        rows.append(
            {
                "dapil": dapil,
                "province": province,
                "party": party,
                "candidate": number,
                "measure": "candidate",
                "votes": _int(texts[-1]),
                "name": " ".join(texts[1:-1]),
                "rank": rank,
                "page": page,
                "box": _box(words[len(texts) - 1]),
                "decree": key,
            }
        )
    return rows


def _seats() -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    dapil = None
    magnitude = 0
    for words in _lines(DECREES["1205"][0]):
        texts = [word["text"] for word in words]
        if (found := _after(texts, "DAERAH PEMILIHAN (DAPIL)")) is not None:
            dapil = found
            continue
        if (found := _after(texts, "JUMLAH KURSI")) is not None:
            magnitude = int(found.split()[0])
            continue
        if texts[:1] == ["LAMPIRAN"] and len(rows) and dapil is not None:
            dapil = None
        if dapil is None or len(texts) < 4 or not texts[0].isdigit() or texts[1] != "Partai":
            continue
        votes = next(text for text in texts[2:] if VOTES.fullmatch(text))
        seats = int(texts[-1]) if re.fullmatch(r"\d{1,2}", texts[-1]) and "," in texts[-2] else 0
        rows.append(
            {"dapil": dapil, "magnitude": magnitude, "party": int(texts[0]), "votes": _int(votes), "seats": seats}
        )
    return (
        pl.DataFrame(rows)
        .group_by("dapil", "party")
        .agg(pl.col("magnitude").first(), pl.col("votes").first(), pl.col("seats").max())
    )


def _elected_rows(key: str) -> dict[str, list[tuple[int, int]]]:
    path = raw.cached(SOURCE, DECREES[key][0]).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    blocks: dict[str, list[tuple[int, int]]] = {}
    dapil = None
    lone: int | None = None
    for line in text.splitlines():
        if match := re.match(r"\s*DAERAH PEMILIHAN\s*:\s*(.+?)\s*$", line):
            dapil = match.group(1)
            blocks.setdefault(dapil, [])
            continue
        if dapil is None:
            continue
        if key == "1208":
            match = re.match(r"\s*\d+\.\s+.*?\s(\d{1,2})\s+\S.*?\s(\d{1,3}(?:[.,]\d{3})*)\s+[IVX]+\s", line)
            if match:
                blocks[dapil].append((int(match.group(1)), _int(match.group(2))))
            continue
        match = re.match(r"\s*\d+\.\s+(?:.*?\s)?(\d{1,3}(?:[.,]\d{3})*)\s+(\d{1,2})(?:\s|$)", line)
        if match:
            blocks[dapil].append((int(match.group(2)), _int(match.group(1))))
        elif lone is not None and (match := re.match(r"\s*\d+\.\s+\D*?\s(\d{1,2})\s+[A-Z][a-z]", line)):
            blocks[dapil].append((int(match.group(1)), lone))
        lone = _int(line.strip()) if re.fullmatch(r"\s*\d{1,3}(?:[.,]\d{3})+\s*", line) else None
    return blocks


def _amended_rows(key: str) -> dict[str, list[tuple[int, int]]]:
    tokens: dict[str, dict[str, list[tuple[int, float, float, str]]]] = {}
    dapil = None
    columns: dict[str, float] = {}
    for words in _lines(DECREES[key][0]):
        texts = [word["text"] for word in words]
        if (found := _after(texts, "DAERAH PEMILIHAN")) is not None:
            dapil = found
            tokens[dapil] = {"row": [], "votes": [], "number": []}
            continue
        if dapil is None or "KURSI" in texts or float(words[0]["top"]) < 60:
            continue
        for word in words:
            left, top, width = float(word["left"]), float(word["top"]), float(word["width"])
            center, page, text = left + width / 2, int(word["page_num"]), word["text"]
            if not tokens[dapil]["row"] and text in ("Suara", "SUARA", "Perolehan", "Sah"):
                columns["votes"] = center
            if not tokens[dapil]["row"] and text == "DCT":
                columns["number"] = center
            if re.fullmatch(r"\d+\.", text) and left < 70:
                tokens[dapil]["row"].append((page, top, center, text))
            elif VOTES.fullmatch(text) and abs(center - columns.get("votes", -999)) < 30:
                tokens[dapil]["votes"].append((page, top, center, text))
            elif re.fullmatch(r"\d{1,2}", text) and abs(center - columns.get("number", -999)) < 15:
                tokens[dapil]["number"].append((page, top, center, text))
    blocks: dict[str, list[tuple[int, int]]] = {}
    for dapil, found in tokens.items():
        counts = {kind: len(cells) for kind, cells in found.items()}
        if len(set(counts.values())) != 1:
            raise ValueError(f"{key} {dapil}: unequal columns {counts}")
        blocks[dapil] = [
            (int(number[3]), _int(votes[3]))
            for votes, number in zip(sorted(found["votes"]), sorted(found["number"]), strict=True)
        ]
    return blocks


def _replaced_1208() -> dict[str, list[int]]:
    path = raw.cached(SOURCE, DECREES["1208"][0]).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    out: dict[str, list[int]] = {}
    for block in re.split(r"DAERAH PEMILIHAN\s*:", text)[1:]:
        dapil = block.splitlines()[0].strip()
        out[dapil] = [int(number) for number in re.findall(r"nomor\s+urut\s+(?:[A-Z]+\s+)*(\d+)", block)]
    return out


def _typo(value: int, printed: int) -> bool:
    left, right = str(value), str(printed)
    return len(left) == len(right) and sum(a != b for a, b in zip(left, right, strict=True)) == 1


def _match(pool: pl.DataFrame, dapil: str, number: int, votes: int) -> int:
    found = pool.filter((pl.col("dapil") == dapil) & (pl.col("candidate") == number) & (pl.col("votes") == votes))
    if found.height == 0:
        near = pool.filter((pl.col("dapil") == dapil) & (pl.col("candidate") == number)).filter(
            pl.col("votes").map_elements(lambda value: _typo(value, votes), return_dtype=pl.Boolean)
        )
        if near.height == 1:
            print(f"{dapil}: candidate {number} printed with {votes} votes for {near['votes'][0]}")
            found = near
    if found.height != 1:
        raise ValueError(f"{dapil}: {found.height} candidates with number {number} and {votes} votes")
    return int(found["party"][0])


def _sex() -> pl.DataFrame:
    path = raw.cached(SOURCE, DECREES["1562"][0]).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    rows: list[tuple[str, int, int, str]] = []
    dapil = party = None
    listing = False
    for line in text.split("\n"):
        if found := re.search(r"DAERAH PEMILIHAN\s*:\s*(.+?)\s*$", line):
            dapil, party, listing = found.group(1), None, False
        elif dapil is not None and party is None and (head := DCT_PARTY.match(line)) and "NAMA" not in line:
            party = int(head.group(1))
        elif "(5)" in line:
            listing = True
        elif listing and party is not None:
            if row := DCT_ROW.match(line):
                rows.append((dapil, party, int(row.group(1)), row.group(2)))
            elif "JUMLAH TOTAL" in line:
                listing, party = False, None
    return pl.DataFrame(rows, schema=["dapil", "party", "candidate", "sex"], orient="row")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames = {key: pl.DataFrame(_votes(key), infer_schema_length=None) for key in ("360", "1050", "1060")}
    partial = frames.pop("1050").select("dapil", "party", "candidate", "measure", "votes")
    later = partial.join(
        frames["1060"].select("dapil", "party", "candidate", "measure", new="votes"),
        on=["dapil", "party", "candidate", "measure"],
        how="left",
    ).filter(pl.col("votes").ne_missing(pl.col("new")))
    print(f"1050 figures that 1060 changes again: {later.height} of {partial.height}")
    for key, frame in frames.items():
        parties = frame.filter(pl.col("measure") != "total").group_by("dapil", "party").agg(pl.col("votes").sum())
        totals = frame.filter(pl.col("measure") == "total").select("dapil", "party", total="votes")
        check = parties.join(totals, on=["dapil", "party"], how="full", coalesce=True).filter(
            pl.col("votes").ne_missing(pl.col("total"))
        )
        if check.height:
            raise ValueError(f"{key}: party and candidate votes do not sum to the printed total: {check.rows()[:5]}")
        print(f"{key}: {frame['dapil'].n_unique()} dapils, {frame.height} rows")
    results = frames["360"]
    for key in ("1060",):
        amended = frames[key]["dapil"].unique()
        if set(frames[key].filter(pl.col("measure") == "party")["party"]) != set(
            results.filter(pl.col("measure") == "party")["party"]
        ):
            raise ValueError(f"{key} does not carry every party")
        changed = (
            results.filter(pl.col("dapil").is_in(amended))
            .select("dapil", "party", "candidate", "measure", "votes")
            .join(
                frames[key].select("dapil", "party", "candidate", "measure", new="votes"),
                on=["dapil", "party", "candidate", "measure"],
                how="full",
                coalesce=True,
            )
            .filter(pl.col("votes").ne_missing(pl.col("new")))
        )
        print(f"{key} amends {sorted(amended)}: {changed.height} figures change")
        results = pl.concat([results.filter(~pl.col("dapil").is_in(amended)), frames[key]])
    if results["dapil"].n_unique() != DAPILS:
        raise ValueError(f"{results['dapil'].n_unique()} dapils, not {DAPILS}")
    totals = results.filter(pl.col("measure") == "total")
    national = totals.group_by("party").agg(pl.col("votes").sum()).sort("party")
    valid = int(national["votes"].sum())
    passed = {party for party, votes in national.rows() if votes * 100 >= THRESHOLD * valid}
    print(f"national valid {valid}; {len(passed)} parties over {THRESHOLD} percent: {sorted(passed)}")
    seats = _seats()
    check = seats.join(totals.select("dapil", "party", total="votes"), on=["dapil", "party"], how="left").filter(
        pl.col("votes").ne_missing(pl.col("total"))
    )
    if check.height:
        raise ValueError(f"decree 1205 votes differ from the amended results: {check.rows()[:8]}")
    if set(seats["party"]) != passed or seats["dapil"].n_unique() != DAPILS:
        raise ValueError("decree 1205 does not list the parties over the threshold in every dapil")
    magnitudes = seats.group_by("dapil").agg(pl.col("magnitude").first(), pl.col("seats").sum())
    if magnitudes.filter(pl.col("magnitude") != pl.col("seats")).height or magnitudes["magnitude"].sum() != SEATS:
        raise ValueError(
            f"decree 1205 seats do not fill {SEATS}: {magnitudes.filter(pl.col('magnitude') != pl.col('seats')).rows()}"
        )
    differ = []
    for (dapil,), group in seats.group_by("dapil"):
        won = _sainte_lague(dict(group.select("party", "votes").rows()), int(group["magnitude"][0]))
        if won != {party: count for party, count in group.select("party", "seats").rows() if count}:
            differ.append(dapil)
    print(f"Sainte-Lague differs from decree 1205 in {sorted(differ)}")
    pool = results.filter(pl.col("measure") == "candidate")
    elected: dict[str, set[tuple[int, int]]] = {}
    for dapil, rows in _elected_rows("1206").items():
        elected[dapil] = {(_match(pool, dapil, number, votes), number) for number, votes in rows}
    declared = {
        dapil: {party: count for party, count in group.select("party", "seats").rows() if count}
        for (dapil,), group in seats.group_by("dapil")
    }

    def tally(dapil: str) -> dict[int, int]:
        counts: dict[int, int] = {}
        for party, _ in elected[dapil]:
            counts[party] = counts.get(party, 0) + 1
        return counts

    for dapil, parties in declared.items():
        if tally(dapil) != parties:
            raise ValueError(f"1206 {dapil}: elected {tally(dapil)} against seats {parties}")
    original = {dapil: set(members) for dapil, members in elected.items()}
    replaced = _replaced_1208()
    for key in AMENDMENTS:
        for dapil, rows in (_elected_rows(key) if key == "1208" else _amended_rows(key)).items():
            if key == "1208":
                ((number, votes),) = rows
                party = _match(pool, dapil, number, votes)
                gone = {member for member in elected[dapil] if member[0] == party and member[1] in replaced[dapil]}
                if len(gone) != 1:
                    raise ValueError(f"1208 {dapil}: {gone} replaced")
                elected[dapil] = (elected[dapil] - gone) | {(party, number)}
            else:
                elected[dapil] = {(_match(pool, dapil, number, votes), number) for number, votes in rows}
            if tally(dapil) != declared[dapil]:
                raise ValueError(f"{key} {dapil}: elected {tally(dapil)} against seats {declared[dapil]}")
    changes = {dapil: (original[dapil] - members, members - original[dapil]) for dapil, members in elected.items()}
    changes = {dapil: change for dapil, change in changes.items() if change[0]}
    for dapil, (out, into) in sorted(changes.items()):
        print(f"{dapil}: {sorted(out)} replaced by {sorted(into)}")
    results.write_parquet(PARSED / "results.parquet")
    seats.write_parquet(PARSED / "seats.parquet")
    sex = _sex()
    candidates = results.filter(pl.col("measure") == "candidate").select("dapil", "party", "candidate")
    if (
        candidates.join(sex, on=["dapil", "party", "candidate"], how="anti").height
        or sex.select("dapil", "party", "candidate").is_duplicated().any()
    ):
        raise ValueError("the DCT does not give exactly one sex for every candidate")
    sex.write_parquet(PARSED / "sex.parquet")
    pl.DataFrame(
        [
            {"dapil": dapil, "party": party, "candidate": number}
            for dapil, members in elected.items()
            for party, number in members
        ]
    ).write_parquet(PARSED / "elected.parquet")
    pl.DataFrame(
        [
            {"dapil": dapil, "party": party, "candidate": number}
            for dapil, (out, _) in changes.items()
            for party, number in out
        ],
        schema={"dapil": pl.String, "party": pl.Int64, "candidate": pl.Int64},
    ).write_parquet(PARSED / "replaced.parquet")
    print(
        f"{results['dapil'].n_unique()} dapils, {pool.height} candidates, "
        f"{sum(len(members) for members in elected.values())} elected, {len(changes)} dapils amended after 1206"
    )


DECREE_LICENSE = "Public record: official decree, no license stated"
SYSTEM = (
    "The 580 seats of the House of Representatives (Dewan Perwakilan Rakyat) are filled in 84 electoral districts "
    "(daerah pemilihan) of 3 to 10 seats under UU 7/2017 as amended by UU 7/2023. Only parties with at least 4 percent "
    "of the valid votes nationwide take part in the allocation (art. 414); each district's seats are divided among "
    "them by the Sainte-Laguë method with divisors 1, 3, 5, 7, ... (art. 415), and each party's seats go to its "
    "candidates with the most votes (art. 422). A voter marks a party or one of its candidates; both count toward the "
    "party."
)
METHODOLOGY = (
    "Party-only votes and candidate votes by electoral district, from lampiran III of KPU decree 360/2024, with Banten "
    "II, Jawa Timur IV, and Kalimantan Timur as replaced after re-votes by decree 1060/2024 (the second amendment; the "
    "first, decree 1050/2024, lists only each party's top candidates there). In every district each party's "
    "party-only and candidate votes sum to its printed total, and the totals of the eight parties over the national "
    "threshold equal those in decree 1205/2024, whose seats Sainte-Laguë reproduces in all 84 districts. KPU's "
    "national decrees publish DPR results by district only. Elected members are from decree 1206/2024 as amended by "
    "decrees 1208, 1309, 1349, 1368, and 1401 of 2024."
)
REPLACED = (
    "KPU replaced members it first declared elected in decree 1206/2024, mostly because they resigned, died, or were "
    "expelled by their party before taking office; the seat went to the party's next candidate by votes. The elected "
    "candidates here follow the last amendment (decrees 1208, 1309, 1349, 1368, and 1401 of 2024), so they are not "
    "always the party's top vote-getters."
)
DAY = DAYS[2024].isoformat()


def _title(dapil: str) -> str:
    title = dapil.title()
    return re.sub(r"\b[IVX]+\b", lambda match: match.group(0).upper(), title, flags=re.IGNORECASE)


def emit() -> Path:
    from ea.changeset import Changeset
    from ea.store import connect

    results = pl.read_parquet(PARSED / "results.parquet").filter(pl.col("measure") != "total")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    elected = set(pl.read_parquet(PARSED / "elected.parquet").rows())
    replaced = set(pl.read_parquet(PARSED / "replaced.parquet")["dapil"])
    sexes = {
        (dapil, party, number): sex for dapil, party, number, sex in pl.read_parquet(PARSED / "sex.parquet").rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's 2024 House of Representatives (DPR) election: party and candidate votes in all 84 "
                "districts, declared seats, and elected members as amended"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        election = cs.key("id.election", DAY)
        body = cs.key("id.body", "dpr")
        if election in cs.minted or body in cs.minted:
            raise ValueError("publish id.kpu_pilpres and id.kpu before id.kpu_dpr_2024")

        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        for key, (url, title) in DECREES.items():
            found = sources[key] = cs.source(KPU, title)
            cs.add(
                "source",
                id=found,
                kind="official_body",
                publisher=KPU,
                title=title,
                url=url,
                license=DECREE_LICENSE,
                license_url="https://jdih.kpu.go.id",
            )
            artifacts[key] = cs.artifact(raw.cached(SOURCE, url), found)

        pages: dict[tuple[str, int], tuple[UUID, int, int]] = {}

        def page(key: str, number: int) -> tuple[UUID, int, int]:
            if (key, number) not in pages:
                cached = raw.cached(SOURCE, DECREES[key][0])
                image = DATA / "pages" / SOURCE / f"{cached.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(cached.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = Image.open(image).size
                found = cs.page(artifacts[key], number)
                cs.add(
                    "source_page",
                    id=found,
                    artifact_id=artifacts[key],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
                pages[key, number] = (found, width, height)
            return pages[key, number]

        system = cs.key("id.electoral_system", "dpr:2024")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=indonesia,
            rules={
                "formula": "sainte_lague",
                "threshold": {"percent": THRESHOLD, "level": "party", "scope": "national"},
                "lists": {"order": "open", "vacancy": "next_by_votes"},
            },
            rules_version=1,
        )
        name(system, "House of Representatives open-list proportional representation, 2024", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        parties = {
            int(number): cs.key("id.party", key)
            for number, key in conn.execute(
                """select c.locator->>'nomor_urut', e.value from citation c
                   join external_id e on e.entity_id = c.entity_id and e.scheme = 'id.party'
                   where c.locator->>'election' = '2024'"""
            ).fetchall()
        }
        if set(results["party"]) - set(parties):
            raise ValueError(f"parties {sorted(set(results['party']) - set(parties))} are not published")

        cs.add(
            "scheme",
            code="id.dapil_dpr",
            polity_id=indonesia,
            kind="electoral_district",
            rank=2,
            noun="electoral district",
            kinds=None,
            code_system=None,
        )
        districts = results.select("dapil", "province").unique()
        whole = {province for province, count in districts.group_by("province").len().rows() if count == 1}
        for dapil, province in sorted(districts.rows()):
            key = f"{DAY}:dpr:{_slug(dapil)}"
            title = _title(dapil)
            area = cs.area("id.provinsi", PROVINCES[province])
            code = f"2024:{_slug(dapil)}"
            unit = cs.unit("id.dapil_dpr", code)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=indonesia,
                scheme_code="id.dapil_dpr",
                code=code,
                area_relation="equals" if province in whole else "within",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
            name(unit, f"Daerah Pemilihan {title}")
            name(unit, f"{title} electoral district", "en", "english")
            contest = cs.key("id.contest", key)
            magnitude = int(seats.filter(pl.col("dapil") == dapil)["magnitude"][0])
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area if province in whole else None,
                term="full",
                magnitude=magnitude,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"Dewan Perwakilan Rakyat, {title}")
            name(contest, f"House of Representatives, {title}", "en", "english")
            cs.add("slug", path=f"id/{DAY}-general/dpr/{_slug(dapil)}", entity_id=contest)
            if dapil in replaced:
                cs.add("note", entity_id=contest, kind="caveat", body_md=REPLACED)
            rows = results.filter(pl.col("dapil") == dapil)
            decree = str(rows["decree"][0])
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[decree],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHODOLOGY)
            won = dict(seats.filter(pl.col("dapil") == dapil).select("party", "seats").rows())
            options: dict[tuple[int, int | None], UUID] = {}
            for party in sorted(set(rows["party"])):
                listed = cs.key("id.option", f"{key}:{party}")
                cs.add(
                    "contest_option",
                    id=listed,
                    contest_id=contest,
                    kind="list",
                    party_id=parties[party],
                    ballot_position=party,
                    ballot_label=None,
                )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=listed,
                    kind="won" if won.get(party) else "lost",
                    seats=won.get(party, 0),
                )
                legend = options[party, None] = cs.key("id.option", f"{key}:{party}:party")
                cs.add(
                    "contest_option",
                    id=legend,
                    contest_id=contest,
                    kind="legend",
                    parent_option_id=listed,
                    party_id=parties[party],
                    ballot_position=None,
                    ballot_label=None,
                )
                for number, ballot in (
                    rows.filter((pl.col("party") == party) & (pl.col("measure") == "candidate"))
                    .sort("candidate")
                    .select("candidate", "name")
                    .rows()
                ):
                    person = cs.key(f"{SOURCE}.person", f"2024:{key}:{party}:{number}")
                    cs.add("person", id=person, gender={"L": "male", "P": "female"}[sexes[dapil, party, number]])
                    if person in cs.minted:
                        name(person, ballot)
                    option = options[party, number] = cs.key("id.option", f"{key}:{party}:{number}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=listed,
                        party_id=parties[party],
                        ballot_position=number,
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("id.candidacy", f"{key}:{party}:{number}"),
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        ballot_name=ballot,
                        is_independent=False,
                        member_of_party_id=parties[party],
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if (dapil, party, number) in elected else "lost",
                        seats=None,
                    )
            for party, number, value, key_, number_, box in rows.select(
                "party", "candidate", "votes", "decree", "page", "box"
            ).rows():
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": options[party, number],
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=value)
                found, width, height = page(key_, number_)
                x0, y0, x1, y1 = (side * 100 / 72 for side in box)
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=found,
                    source_id=None,
                    x0=x0 / width,
                    y0=y0 / height,
                    x1=x1 / width,
                    y1=y1 / height,
                )
        return cs.write()
