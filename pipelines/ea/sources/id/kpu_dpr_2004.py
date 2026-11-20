import html
import re
import struct
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.sources.id.kpu_dpr import _slug
from ea.store import DATA

SOURCE = "id.kpu_dpr_2004"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KPU = "Komisi Pemilihan Umum Republik Indonesia"
WAYBACK = "https://web.archive.org/web/"
SITE = "http://www.kpu.go.id:80/"
SUMMARY = f"{WAYBACK}20040803205141id_/{SITE}suara/hasilsuara_dprri_dapil_parpol_sah.php"
NATIONAL = f"{WAYBACK}20040611224915id_/{SITE}suara/dprkursi.php"
CDX = (
    "https://web.archive.org/cdx/search/cdx?url=www.kpu.go.id/suara/hasilsuara_dapil_partai.php&matchType=prefix"
    "&from=2004&to=2008&filter=statuscode:200&fl=original,timestamp,length&limit=20000"
)
ELECTED = tuple(
    f"{WAYBACK}20060220{stamp}id_/{SITE}berita/link/CalonTerpilih_{name}.doc"
    for stamp, name in (
        ("043752", "AnggotaDPR_1"),
        ("043633", "ANggotaDPR_2"),
        ("043658", "AnggotaDPR_3"),
        ("043654", "AnggotaDPR_4"),
    )
)
ANNOUNCED = f"{WAYBACK}20040925100538id_/{SITE}penetapanhasil/lihat-dalam.php?ID=6&cat=penetapanhasil"
DAY = "2004-04-05"
SEATS = 550
DAPILS = 69
PROVINCES = {"20": "21", "91": "92+96", "94": "91+93+94+95", "64": "64+65", "73": "73+76"}
PARTIES: dict[int, tuple[str, str, str, str | None, str | None, str | None]] = {
    1: ("pni-marhaenisme", "Partai Nasional Indonesia Marhaenisme", "PNI Marhaenisme", None, "Q4261536", None),
    2: ("pbsd", "Partai Buruh Sosial Demokrat", "PBSD", None, None, None),
    3: ("pbb", "", "", None, None, None),
    4: ("merdeka", "Partai Merdeka", "Merdeka", "Freedom Party", "Q1136182", None),
    5: ("ppp", "", "", None, None, None),
    6: ("pdk", "Partai Persatuan Demokrasi Kebangsaan", "PDK", "Democratic Nationhood Party", "Q4261748", None),
    7: ("pib", "Partai Perhimpunan Indonesia Baru", "PIB", "New Indonesia Party", "Q12503306", None),
    8: ("pnbk", "Partai Nasional Banteng Kemerdekaan", "PNBK", None, "Q4261024", None),
    9: ("demokrat", "", "", None, None, None),
    10: ("pkpi", "", "", None, None, None),
    11: (
        "ppdi",
        "Partai Penegak Demokrasi Indonesia",
        "PPDI",
        "Indonesian Democratic Vanguard Party",
        "Q4261342",
        None,
    ),
    12: ("pnui", "Partai Persatuan Nahdlatul Ummah Indonesia", "PNUI", None, "Q4261054", None),
    13: ("pan", "", "", None, None, None),
    14: ("pkpb", "Partai Karya Peduli Bangsa", "PKPB", "Concern for the Nation Functional Party", "Q5158454", "4209"),
    15: ("pkb", "", "", None, None, None),
    16: ("pks", "", "", None, None, None),
    17: ("pbr", "Partai Bintang Reformasi", "PBR", "Reform Star Party", "Q4261778", "4212"),
    18: ("pdip", "", "", None, None, None),
    19: ("pds", "Partai Damai Sejahtera", "PDS", "Prosperous Peace Party", "Q7250923", "4211"),
    20: ("golkar", "", "", None, None, None),
    21: ("patriot", "Partai Patriot Pancasila", "Patriot Pancasila", "Patriot Party", "Q4261517", None),
    22: ("psi-sarikat", "Partai Sarikat Indonesia", "PSI", "Indonesian Unity Party", "Q4261029", None),
    23: ("ppd", "Partai Persatuan Daerah", "PPD", "Regional Unity Party", "Q7309185", None),
    24: ("pelopor", "Partai Pelopor", "Pelopor", "Pioneers' Party", "Q7196857", None),
}
MANUAL = {
    "H. Dudhie Makmun Murod, MBA": ("SUMSEL - II", 18),
    "Djoemad Cipto Wardoyo": ("JAWA BARAT - VIII", 18),
    "Marnarar Sirait, SIP": ("JAWA BARAT - VIII", 18),
    "Mutammimul Ula, SH": ("JAWA TENGAH - V", 16),
    "Ir. H. Suswono": ("JAWA TENGAH - IX", 16),
    "Hj. Zunatul Mafruchah, SH": ("YOGYAKARTA", 15),
    "Arbab Papoeka, SH": ("SULTRA", 13),
    "Drs. H.A. Razak Porozi": ("SULTRA", 18),
    "Prof.Drs.H.Rustam E. Tamburaka": ("SULTRA", 20),
    "Dra. Hj. Mustika Rahim": ("SULTRA", 20),
    "Alexander Litaay": ("MALUKU", 18),
}
SHORT = {"JAWA BARAT - X": 1}
ABBREVIATIONS = {
    "NAD": "Nanggroe Aceh Darussalam",
    "SUMUT": "Sumatera Utara",
    "SUMBAR": "Sumatera Barat",
    "SUMSEL": "Sumatera Selatan",
    "KALBAR": "Kalimantan Barat",
    "KALTENG": "Kalimantan Tengah",
    "KALSEL": "Kalimantan Selatan",
    "KALTIM": "Kalimantan Timur",
    "SULUT": "Sulawesi Utara",
    "SULTENG": "Sulawesi Tengah",
    "SULSEL": "Sulawesi Selatan",
    "SULTRA": "Sulawesi Tenggara",
    "NTB": "Nusa Tenggara Barat",
    "NTT": "Nusa Tenggara Timur",
    "IRJABAR": "Irian Jaya Barat",
    "YOGYAKARTA": "Daerah Istimewa Yogyakarta",
}
LICENSE = "Public record: official publication, no license stated"
SYSTEM = (
    "The 550 seats of the House of Representatives (Dewan Perwakilan Rakyat) were filled in 69 electoral districts "
    "(daerah pemilihan) of 3 to 12 seats under UU 12/2003. A voter marked a party, or a party and one of its "
    "candidates. Each district's seats went to parties by the largest remainder of the Hare quota (bilangan pembagi "
    "pemilihan). Within a party, a candidate whose votes reached the quota was elected first; the "
    "party's other seats went down its list in ballot order."
)
METHODOLOGY = (
    "Party-only votes and candidate votes by electoral district and party, from KPU's results pages as archived by "
    "the Internet Archive between August 2004 and 2006. On each page the candidate votes sum to the printed votes for "
    "party and candidate, and these plus the party-only votes equal the party's total in KPU's district summary. "
    "Party-only votes are KPU's own remainder (total less candidate votes) and are negative on a few pages; they are "
    "stored as printed. Elected members are from KPU's list of 3 August 2004, after the Constitutional Court's rulings "
    "on disputed results, matched to the candidates by name. Seats are those of the elected members, which differ in "
    "a few districts from the 11 June 2004 seat table because of those rulings."
)
MISSING = (
    "The Internet Archive holds no copy of KPU's candidate page for some parties in this district. Their total votes "
    "come from KPU's district summary and are not split into party-only and candidate votes; their elected members "
    "are listed without a ballot number or votes."
)
SHORTFALL = (
    "KPU's list of elected members of 3 August 2004 names 9 members for this district, but its seat table sums to 550 "
    "only with 10 here, and the Hare quota gives Golkar a third seat. The tenth member is not recorded."
)
CONTINUED = {"Adjie Massaid"}
SWAPPED = ("BANGKA BELITUNG", "KEPULAUAN RIAU")
TITLES = {
    "summary": "Rekapitulasi Perolehan Suara Sah untuk DPR-RI (kpu.go.id, as archived 3 August 2004)",
    "pages": "Perolehan Suara Caleg per Daerah Pemilihan per Parpol (kpu.go.id, as archived August 2004 to 2006)",
    "national": (
        "Perhitungan Perolehan Kursi Partai Politik Peserta Pemilihan Umum Anggota Dewan Perwakilan Rakyat dalam "
        "Pemilihan Umum Tahun 2004 (kpu.go.id, as archived 11 June 2004)"
    ),
    "elected": "Nama-nama Calon Terpilih Anggota Dewan Perwakilan Rakyat Pemilihan Umum 2004 (KPU, 3 August 2004)",
}


def _get(url: str, suffix: str) -> raw.Artifact | None:
    for attempt in range(8):
        try:
            found = raw.fetch(SOURCE, url, suffix=suffix)
            time.sleep(1)
            return found
        except httpx.HTTPError as error:
            print(f"{url}: {error!r}", flush=True)
            time.sleep(60 * (attempt + 1))
    return None


def _text(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_bytes().decode("cp1252")


def _cells(row: str) -> list[str]:
    return [
        html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell))).strip()
        for cell in re.findall(r"<td.*?(?=<td|</tr|<tr|$)", row, flags=re.DOTALL | re.IGNORECASE)
    ]


def _summary() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in re.findall(r"<tr.*?</tr>", _text(SUMMARY), flags=re.DOTALL | re.IGNORECASE):
        codes = re.search(r"fprop=(\d+)&fpml=(\d+)", row)
        if codes is None or "fpartai" not in row:
            continue
        cells = _cells(row)
        if len(cells) != 26:
            raise ValueError(f"summary row {cells[:2]}: {len(cells)} cells")
        values = [int(cell.replace(",", "")) for cell in cells[1:]]
        if sum(values[:-1]) != values[-1]:
            raise ValueError(f"{cells[0]}: parties sum to {sum(values[:-1])}, not {values[-1]}")
        for party, votes in enumerate(values[:-1], 1):
            rows.append({"prop": codes[1], "pml": codes[2], "dapil": cells[0], "party": party, "total": votes})
    return rows


def _captures() -> dict[tuple[str, str, int], str]:
    found: dict[tuple[str, str, int], str] = {}
    for line in _text(CDX).splitlines():
        url, stamp, _ = line.split(" ")
        match = re.search(r"fprop=(\d+)&fpml=(\d+)&fpartai=(\d+)$", url)
        if match:
            found.setdefault((match[1], match[2], int(match[3])), stamp)
    return found


def _page(prop: str, pml: str, party: int, stamp: str) -> str:
    return f"{WAYBACK}{stamp}id_/{SITE}suara/hasilsuara_dapil_partai.php?fprop={prop}&fpml={pml}&fpartai={party:02d}"


def fetch() -> None:
    for url in (SUMMARY, NATIONAL, CDX, ANNOUNCED, *ELECTED):
        _get(url, ".doc" if url.endswith(".doc") else ".txt" if url == CDX else ".html")
    captures = _captures()
    known = raw.keys(SOURCE)
    wanted = [
        url
        for row in _summary()
        if row["total"]
        and (key := (str(row["prop"]), str(row["pml"]), int(str(row["party"])))) in captures
        and (url := _page(*key, captures[key])) not in known
    ]
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda url: _get(url, ".html"), wanted))


def _candidates(url: str) -> tuple[str, str, list[tuple[int, str, int]], int, int]:
    text = _text(url)
    flat = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))
    head = re.search(r"Daerah Pemilihan\s*:\s*(.*?)\s*Parpol\s*:\s*(.*?)\s*NO\. URUT", flat)
    if head is None:
        raise ValueError(f"{url}: no heading")
    listed: list[tuple[int, str, int]] = []
    for row in re.split(r"<tr", text, flags=re.IGNORECASE):
        cells = _cells(row)
        if len(cells) == 3 and re.fullmatch(r"\d+\.", cells[0]) and re.fullmatch(r"[\d,]+", cells[2]):
            listed.append((int(cells[0].rstrip(".")), cells[1], int(cells[2].replace(",", ""))))
    both = re.search(r"Mencoblos Partai dan Caleg\s*([\d,]+)", flat)
    only = re.search(r"Mencoblos Partai Saja\s*(-?[\d,]+)", flat)
    total = re.search(r"Jumlah Suara\s*([\d,]+)\s*$", flat.strip())
    if both is None or only is None or total is None:
        raise ValueError(f"{url}: no totals")
    both_, only_, total_ = (int(match[1].replace(",", "")) for match in (both, only, total))
    if sum(votes for _, _, votes in listed) != both_ or both_ + only_ != total_:
        raise ValueError(f"{url}: candidates {sum(v for _, _, v in listed)}, printed {both_} + {only_} = {total_}")
    return head[1], head[2], listed, only_, total_


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("’", "'")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\(.*?\)", " ", text)
    words = re.findall(r"[a-z']+", text)
    titles = {
        "h",
        "hj",
        "hc",
        "ir",
        "dr",
        "drs",
        "dra",
        "drg",
        "drh",
        "prof",
        "se",
        "sh",
        "mh",
        "ma",
        "ms",
        "msi",
        "si",
        "mm",
        "mba",
        "msc",
        "sc",
        "ag",
        "sag",
        "spd",
        "pd",
        "sip",
        "sos",
        "ssos",
        "ak",
        "kh",
        "k",
        "lc",
        "mt",
        "mmt",
        "me",
        "mec",
        "mkn",
        "kn",
        "mpa",
        "phd",
        "ph",
        "d",
        "llm",
        "ll",
        "m",
        "s",
        "spb",
        "spog",
        "apt",
        "ba",
        "bsc",
        "b",
        "mdiv",
        "mma",
        "mtp",
        "ss",
        "tgk",
        "st",
        "ny",
        "cn",
        "hum",
        "psi",
        "shi",
        "spsi",
        "bba",
        "ipb",
        "pol",
        "purn",
        "kol",
        "mayjen",
        "majen",
        "gbph",
        "mc",
        "mag",
        "mpd",
        "sp",
        "mp",
        "ec",
        "tks",
        "a",
        "ml",
        "lml",
    }
    return " ".join(word for word in words if word not in titles and len(word) > 1)


def _word(path: Path) -> str:
    """The main text of a Word 97 file: the pieces its piece table lists, each 8-bit (cp1252) or UTF-16, read from
    the WordDocument stream of the compound file."""
    data = path.read_bytes()
    if data[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
        raise ValueError(f"{path.name}: not a compound file")
    size = 1 << struct.unpack_from("<H", data, 30)[0]
    count, directory = struct.unpack_from("<II", data, 44)
    if count > 109:
        raise ValueError(f"{path.name}: {count} allocation sectors")

    def sector(number: int) -> bytes:
        return data[512 + number * size : 512 + (number + 1) * size]

    links = [
        link
        for at in struct.unpack_from(f"<{count}I", data, 76)
        for link in struct.unpack(f"<{size // 4}I", sector(at))
    ]

    def chain(number: int) -> bytes:
        out = bytearray()
        while number < 0xFFFFFFFA:
            out += sector(number)
            number = links[number]
        return bytes(out)

    streams: dict[str, bytes] = {}
    entries = chain(directory)
    cutoff = struct.unpack_from("<I", data, 56)[0]
    for at in range(0, len(entries), 128):
        entry = entries[at : at + 128]
        name = entry[: max(struct.unpack_from("<H", entry, 64)[0] - 2, 0)].decode("utf-16-le")
        start, length = struct.unpack_from("<II", entry, 116)
        if name in ("WordDocument", "0Table", "1Table") and entry[66] == 2:
            if length < cutoff:
                raise ValueError(f"{path.name}: {name} is in the mini stream")
            streams[name] = chain(start)[:length]
    document = streams["WordDocument"]
    if struct.unpack_from("<H", document, 0)[0] != 0xA5EC:
        raise ValueError(f"{path.name}: not a Word 97 file")
    table = streams["1Table" if struct.unpack_from("<H", document, 10)[0] & 0x200 else "0Table"]
    text = struct.unpack_from("<i", document, 0x4C)[0]
    at, size = struct.unpack_from("<II", document, 0x1A2)
    clx = table[at : at + size]
    while clx[0] == 1:
        clx = clx[3 + struct.unpack_from("<H", clx, 1)[0] :]
    if clx[0] != 2:
        raise ValueError(f"{path.name}: no piece table")
    plc = clx[5 : 5 + struct.unpack_from("<I", clx, 1)[0]]
    pieces = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{pieces + 1}i", plc)
    out: list[str] = []
    for piece in range(pieces):
        start, end = cps[piece], min(cps[piece + 1], text)
        if start >= end:
            break
        fc = struct.unpack_from("<I", plc, 4 * (pieces + 1) + 8 * piece + 2)[0]
        if fc & 0x40000000:
            at = (fc & ~0x40000000) // 2
            out.append(document[at : at + end - start].decode("cp1252"))
        else:
            out.append(document[fc : fc + 2 * (end - start)].decode("utf-16-le"))
    return "".join(out)


def _elected() -> list[str]:
    """The elected members in each list's order: every line of the third column of its one table, whose cells end
    in \\x07 and whose rows end in one more."""
    names: list[str] = []
    for url in ELECTED:
        cells = _word(raw.cached(SOURCE, url).path).split("\x07")
        for row in range(0, len(cells) - 3, 4):
            if cells[row + 3]:
                raise ValueError(f"{url}: a row of more than 3 cells")
            for line in re.split(r"[\r\x0b]", cells[row + 2]):
                line = line.strip()
                if line and line != "NAMA CALON TERPILIH":
                    names.append(line)
    return names


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    summary = pl.DataFrame(_summary())
    if summary["dapil"].n_unique() != DAPILS:
        raise ValueError(f"{summary['dapil'].n_unique()} dapils in the summary")
    captures = _captures()
    keys = raw.keys(SOURCE)
    rows: list[dict[str, object]] = []
    missing: list[tuple[str, int, int]] = []
    names: dict[int, str] = {}
    for prop, pml, dapil, party, total in summary.rows():
        if not total:
            continue
        stamp = captures.get((prop, pml, party))
        url = _page(prop, pml, party, stamp) if stamp else None
        if url is None or url not in keys:
            missing.append((dapil, party, total))
            continue
        heading, party_name, listed, only, printed = _candidates(url)
        if heading.upper() != dapil.upper() or printed != total:
            raise ValueError(f"{url}: {heading} {printed} against {dapil} {total}")
        names[party] = party_name
        base = {"prop": prop, "pml": pml, "dapil": dapil, "party": party, "url": url}
        rows.append({**base, "candidate": None, "name": None, "votes": only})
        rows.extend({**base, "candidate": number, "name": name, "votes": votes} for number, name, votes in listed)
    results = pl.DataFrame(rows)
    print(f"{results.height} rows from {results.select('dapil', 'party').n_unique()} pages; {len(missing)} missing")
    for dapil, party, total in missing:
        print(f"  no capture: {dapil} party {party} ({total} votes)")
    pool = results.filter(pl.col("candidate").is_not_null()).with_columns(
        fold=pl.col("name").map_elements(_fold, return_dtype=pl.String)
    )
    order = summary["dapil"].unique(maintain_order=True).to_list()
    first, second = (order.index(dapil) for dapil in SWAPPED)
    order[first], order[second] = order[second], order[first]
    by_dapil = {
        dapil: list(group.select("party", "candidate", "fold").iter_rows())
        for (dapil,), group in pool.group_by("dapil")
    }
    listed = [name for name in _elected() if _fold(name) and name not in CONTINUED]
    best: list[list[tuple[float, int, int]]] = []
    for name in listed:
        target = _fold(name)
        row = []
        for dapil in order:
            scored = max(
                (
                    (SequenceMatcher(None, target, fold).ratio(), party, number)
                    for party, number, fold in by_dapil[dapil]
                ),
                default=(0.0, 0, 0),
            )
            row.append(scored)
        best.append(row)
    floor = 0.55
    width = len(order)
    score = [[0.0] * width for _ in listed]
    back: list[list[int]] = [[0] * width for _ in listed]
    for i in range(len(listed)):
        running, at = -1.0, 0
        for d in range(width):
            before = score[i - 1][d] if i else 0.0
            if before > running:
                running, at = before, d
            score[i][d] = running + max(best[i][d][0], floor)
            back[i][d] = at
    d = max(range(width), key=lambda column: score[-1][column])
    path = [0] * len(listed)
    for i in range(len(listed) - 1, -1, -1):
        path[i] = d
        d = back[i][d]
    elected: list[dict[str, object]] = []
    for i, name in enumerate(listed):
        dapil = order[path[i]]
        ratio, party, number = best[i][path[i]]
        if name in MANUAL:
            dapil, party = MANUAL[name]
            number = None
        elif ratio < floor:
            print(f"  unmatched {name!r} near {dapil} (best {ratio:.2f})")
            continue
        elif ratio < 0.8:
            print(f"  weak match {name!r} in {dapil} -> party {party} number {number} ({ratio:.2f})")
        elected.append({"dapil": dapil, "party": party, "candidate": number, "listed": name, "score": round(ratio, 3)})
    winners = pl.DataFrame(elected)
    duplicated = winners.filter(
        pl.col("candidate").is_not_null() & pl.struct("dapil", "party", "candidate").is_duplicated()
    )
    if duplicated.height:
        raise ValueError(f"matched twice: {duplicated.rows()}")
    if winners.height + sum(SHORT.values()) != SEATS:
        winners.write_parquet(PARSED / "elected.parquet")
        raise ValueError(f"{winners.height} elected, not {SEATS - sum(SHORT.values())}")
    seats = winners.group_by("dapil", "party").len("seats")
    national = {
        int(number): int(count)
        for number, count in re.findall(
            r"<td[^>]*>\s*(\d+)\.\s*</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>[\d.]+</td>\s*<td[^>]*>\s*(\d+)\s*</td>",
            _text(NATIONAL),
        )
    }
    declared = dict(seats.group_by("party").agg(pl.col("seats").sum()).rows())
    print(
        f"party seats differing from the 11 June table: {[(p, national.get(p), declared.get(p)) for p in range(1, 25) if national.get(p, 0) != declared.get(p, 0)]}"
    )
    summary.write_parquet(PARSED / "summary.parquet")
    results.write_parquet(PARSED / "results.parquet")
    winners.write_parquet(PARSED / "elected.parquet")
    seats.write_parquet(PARSED / "seats.parquet")
    pl.DataFrame([{"party": party, "name": name} for party, name in sorted(names.items())]).write_parquet(
        PARSED / "parties.parquet"
    )
    pl.DataFrame(missing, schema=["dapil", "party", "total"], orient="row").write_parquet(PARSED / "missing.parquet")
    print(f"{summary['dapil'].n_unique()} dapils, {pool.height} candidates, {winners.height} elected")


def _title(dapil: str) -> str:
    head, _, number = dapil.partition(" - ")
    head = ABBREVIATIONS.get(head, head.title().replace("Dki", "DKI"))
    return f"{head} {number}" if number else head


def emit() -> Path:
    from ea.package import Draft
    from ea.store import connect

    summary = pl.read_parquet(PARSED / "summary.parquet")
    results = pl.read_parquet(PARSED / "results.parquet")
    winners = pl.read_parquet(PARSED / "elected.parquet")
    seats = {(dapil, party): count for dapil, party, count in pl.read_parquet(PARSED / "seats.parquet").rows()}
    missing = {(dapil, party) for dapil, party, _ in pl.read_parquet(PARSED / "missing.parquet").rows()}
    elected = {(dapil, party, number) for dapil, party, number in winners.select("dapil", "party", "candidate").rows()}
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's 2004 House of Representatives (DPR) election: party and candidate votes in all 69 "
                "districts, seats, and elected members"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        nation = cs.area("id.nation", "ID")
        election = cs.key("id.election", DAY)
        body = cs.key("id.body", "dpr")
        if election in cs.minted or body in cs.minted:
            raise ValueError("publish id.kpu_dpd_early and id.kpu before id.kpu_dpr_2004")

        sources: dict[str, UUID] = {}
        urls = {"summary": SUMMARY, "national": NATIONAL, "elected": ELECTED[0], "pages": f"{SITE}suara/"}
        for key, title in TITLES.items():
            found = sources[key] = cs.source(KPU, title)
            cs.add(
                "source",
                id=found,
                kind="official_body",
                publisher=KPU,
                title=title,
                url=urls[key],
                license=LICENSE,
                license_url="https://www.kpu.go.id",
            )
        for url in (SUMMARY,):
            cs.artifact(raw.cached(SOURCE, url), sources["summary"])
        cs.artifact(raw.cached(SOURCE, NATIONAL), sources["national"])
        for url in (*ELECTED, ANNOUNCED):
            cs.artifact(raw.cached(SOURCE, url), sources["elected"])
        for url in sorted(set(results["url"])):
            cs.artifact(raw.cached(SOURCE, url), sources["pages"])

        system = cs.key("id.electoral_system", "dpr:2004")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=indonesia,
            rules={"formula": "hare", "lists": {"order": "flexible", "quota": 1, "vacancy": "next_on_list"}},
            rules_version=1,
        )
        name(system, "House of Representatives list proportional representation, 2004", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        parties: dict[int, UUID] = {}
        for number, (key, native, short, english, item, partyfacts) in PARTIES.items():
            party = parties[number] = cs.key("id.party", key)
            if party in cs.minted:
                if not native:
                    raise ValueError(f"party {key} is not published")
                cs.add("party", id=party, kind="party", polity_id=indonesia)
                name(party, native)
                name(party, short, "id", "abbreviation")
                if english is not None:
                    name(party, english, "en", "english")
                if item is not None:
                    cs.add("external_id", entity_id=party, scheme="wikidata", value=item)
                if partyfacts is not None:
                    cs.add("external_id", entity_id=party, scheme="partyfacts", value=partyfacts)
            cs.add(
                "citation",
                entity_id=party,
                source_id=sources["summary"],
                locator={"election": "2004", "nomor_urut": number},
            )

        districts = summary.select("prop", "dapil").unique()
        counts = dict(districts.group_by("prop").len().rows())
        for prop, dapil in sorted(districts.rows(), key=lambda row: row[1]):
            slug = _slug(dapil)
            key = f"{DAY}:dpr:{slug}"
            title = _title(dapil)
            code = PROVINCES.get(prop, prop)
            spanning = "+" in code
            whole = not spanning and counts[prop] == 1
            area = nation if spanning else cs.area("id.provinsi", code)
            unit = cs.unit("id.dapil_dpr", f"2004:{slug}")
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=indonesia,
                scheme_code="id.dapil_dpr",
                code=f"2004:{slug}",
                area_relation="equals" if whole else "within",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
            name(unit, f"Daerah Pemilihan {title}")
            name(unit, f"{title} electoral district", "en", "english")
            contest = cs.key("id.contest", key)
            won = {party: count for (where, party), count in seats.items() if where == dapil}
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area if whole else None,
                term="full",
                magnitude=sum(won.values()) + SHORT.get(dapil, 0),
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"Dewan Perwakilan Rakyat, {title}")
            name(contest, f"House of Representatives, {title}", "en", "english")
            cs.add("slug", path=f"id/{DAY}-legislative/dpr/{slug}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources["pages"],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHODOLOGY)
            if any(where == dapil for where, _ in missing):
                cs.add("note", entity_id=result_set, kind="caveat", body_md=MISSING)
            if dapil in SHORT:
                cs.add("note", entity_id=contest, kind="caveat", body_md=SHORTFALL)
            rows = results.filter(pl.col("dapil") == dapil)
            totals = summary.filter((pl.col("dapil") == dapil) & (pl.col("total") > 0)).select("party", "total").rows()

            def value(option: UUID, figure: int) -> None:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=figure,
                )

            def candidate(listed: UUID, party: int, tag: str, number: int | None, ballot: str) -> UUID:
                person = cs.key(f"{SOURCE}.person", f"{key}:{party}:{tag}")
                if person in cs.minted:
                    cs.add("person", id=person, gender=None)
                    name(person, ballot)
                option = cs.key("id.option", f"{key}:{party}:{tag}")
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
                    id=cs.key("id.candidacy", f"{key}:{party}:{tag}"),
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    ballot_name=ballot,
                    is_independent=False,
                    member_of_party_id=parties[party],
                )
                return option

            for party, total in totals:
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
                if (dapil, party) in missing:
                    value(listed, total)
                    for (ballot,) in (
                        winners.filter((pl.col("dapil") == dapil) & (pl.col("party") == party)).select("listed").rows()
                    ):
                        option = candidate(listed, party, _slug(_fold(ballot)), None, ballot)
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
                    continue
                legend = cs.key("id.option", f"{key}:{party}:party")
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
                for number, ballot, votes in (
                    rows.filter(pl.col("party") == party)
                    .sort("candidate", nulls_last=False)
                    .select("candidate", "name", "votes")
                    .rows()
                ):
                    if number is None:
                        value(legend, votes)
                        continue
                    option = candidate(listed, party, str(number), number, ballot)
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if (dapil, party, number) in elected else "lost",
                        seats=None,
                    )
                    value(option, votes)
        return cs.write()
