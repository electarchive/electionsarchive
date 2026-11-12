import csv
import difflib
import hashlib
import html
import io
import re
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "tr.muhtar_2024"
VERSION = "1"
DAY = date(2024, 3, 31)
REGISTER = Path(__file__).with_name("muhtarlik_2024.tsv")
BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
COLUMNS = ("no", "ilce", "belde", "kind", "name", "first", "last")
ROW = re.compile(r"^\s*(\d+)\s+AFYONKARAH")
AFYON = "https://www.afyonkarahisar.gov.tr/kurumlar/afyonkarahisar.gov.tr/Dosyalar/" + quote("Seçilmişler")
UNDATED = (
    "The list carries no election date and was published in 2026, so a muhtar who took office after a by-election "
    "or an appointment may be listed instead of the 2024 winner."
)
TAIL = (
    " YSK publishes no muhtar vote counts, so each contest carries the winner only. Each listed muhtarlık is joined to "
    "its YSK muhtarlık code by ilçe, kind, and name, and by belde where the list gives one; a spelling variant is "
    "joined only when it is the one close match left in its ilçe. Candidates for muhtar stand without party labels."
)


def fold(text: str) -> str:
    text = text.replace("İ", "I").upper().translate(str.maketrans("ŞĞÜÖÇÂÎÛ", "SGUOCAIU"))
    return re.sub(r"[^A-Z0-9]", "", text)


def stem(name: str, belde: str) -> str:
    text = re.sub(r"\s*\(.*?\)", "", name.strip()).replace("i", "İ").replace("ı", "I").upper()
    text = re.sub(r"(\s+MAH\.?|\s+MAHALLESİ|\s+MAHALLE|\s+KÖYÜ|\s+KÖY)$", "", text)
    if belde:
        text = re.sub(r"^" + re.escape(belde.upper()) + r"\s*-\s*", "", text)
    return fold(text)


def kind_of(name: str) -> str:
    folded = fold(name.split("(")[0])
    return "köy" if re.search(r"KOYU?$", folded) else "mahalle" if re.search(r"MAH(ALLESI?|ALLE)?$", folded) else ""


def line(no: str, ilce: str, belde: str, kind: str, name: str, person: str) -> dict[str, str]:
    ilce, _, split = ilce.partition(" / ")
    belde = split or belde
    return dict(zip(COLUMNS, (no, ilce, belde, kind, name, " ".join(person.split()), "")))


def pdf_text(pdf: Path, *args: str) -> list[str]:
    return subprocess.run(
        ["pdftotext", *args, str(pdf), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()


def cells(page: str) -> list[list[str]]:
    return [
        [
            " ".join(html.unescape(re.sub(r"<.*?>", " ", cell)).split())
            for cell in re.findall(r"<t[dh].*?</t[dh]>", row, re.DOTALL)
        ]
        for row in re.findall(r"<tr.*?</tr>", page, re.DOTALL)
    ]


def afyon_pdf(pdf: Path, kind: str) -> list[dict[str, str]]:
    lines = pdf_text(pdf, "-layout")
    rows = []
    for index, text in enumerate(lines):
        if not ROW.match(text):
            continue
        fields = re.split(r"\s{2,}", text.strip())
        if kind == "mahalle":
            no, _, ilce, belde, name, population, *rest = fields
            if not population.isdigit():
                belde, _, joined = belde.partition(" ")
                rest = [population, *rest]
                name = joined
        else:
            no, _, ilce, name, _, *rest = fields
            belde = ""
            rest = [field for field in rest if not re.fullmatch(r"\d{11}", field)]
        rest = [field for field in rest if not re.fullmatch(r"[\d ]{10,16}", field)]
        if len(rest) == 2:
            first, last = rest
        elif len(rest) == 1:
            above = lines[index - 1].strip() if index and not ROW.match(lines[index - 1]) else ""
            below = lines[index + 1].strip() if index + 1 < len(lines) and not ROW.match(lines[index + 1]) else ""
            first, last = rest[0], " ".join(part for part in (above, below) if part)
        else:
            raise ValueError(f"unread {kind} row {no}")
        if re.search(r"\d", first + last):
            raise ValueError(f"digits in the name on {kind} row {no}")
        rows.append(dict(zip(COLUMNS, (no, ilce, "" if belde == "MERKEZ" else belde, kind, name, first, last))))
    return rows


def kocaeli_pdf(pdf: Path, _: str) -> list[dict[str, str]]:
    pages: dict[str, list[tuple[float, float, str]]] = {}
    for text in pdf_text(pdf, "-tsv")[1:]:
        f = text.split("\t", 11)
        if f[0] == "5" and len(f) == 12:
            pages.setdefault(f[1], []).append((float(f[6]), float(f[7]), f[11]))
    rows = []
    for page in pages.values():
        starts = sorted((top, word) for left, top, word in page if left < 100 and word.isdigit())
        for index, (top, no) in enumerate(starts):
            stop = starts[index + 1][0] - 4 if index + 1 < len(starts) else float("inf")
            band = sorted((t, left, word) for left, t, word in page if top - 4 <= t < stop and left >= 100)
            columns = ([], [], [])
            for _y, left, word in band:
                columns[0 if left < 180 else 1 if left < 320 else 2].append(word)
            rows.append(line(no, " ".join(columns[0]), "", "", " ".join(columns[1]), " ".join(columns[2])))
    return rows


def manisa_pdf(pdf: Path, _: str) -> list[dict[str, str]]:
    rows = []
    for text in pdf_text(pdf, "-layout"):
        f = re.split(r"\s{2,}", text.strip())
        if len(f) >= 6 and f[0].isdigit() and f[3] == "MANİSA":
            rows.append(line(f[0], f[4], "", "", f[5], f[2]))
    return rows


def kirikkale_pdf(pdf: Path, _: str) -> list[dict[str, str]]:
    rows = []
    for text in pdf_text(pdf, "-layout"):
        f = re.split(r"\s{2,}", text.strip())
        if len(f) >= 4 and f[0].isdigit() and not f[1].isdigit():
            rows.append(line(f[0], f[1], "", kind_of(f[2]), f[2], f[3]))
    return rows


def duzce_html(page: str, _: str) -> list[dict[str, str]]:
    rows = []
    ilce = kind = ""
    for c in cells(page):
        if len(c) == 1 and re.search(r"MUHTAR L[İI]STES[İI]", c[0]):
            ilce = c[0].split()[0]
            kind = "mahalle" if "MAHALLE" in c[0] else "köy"
        elif len(c) == 4 and c[0].isdigit() and ilce:
            rows.append(line(c[0], ilce, "", kind, c[1], c[2]))
    return rows


def karaman_html(page: str, _: str) -> list[dict[str, str]]:
    return [
        line(c[0], c[1].replace("K.Karabekir", "Kazımkarabekir"), "", "", c[2], c[3])
        for c in cells(page)
        if len(c) == 5 and c[0].isdigit()
    ]


def aksaray_html(page: str, _: str) -> list[dict[str, str]]:
    rows = []
    ilce = kind = ""
    for c in cells(page):
        if len(c) == 1 and re.search(r"MUHTAR", c[0]) and not re.search(r"L[İI]STES[İI]", c[0]):
            ilce = "MERKEZ" if c[0].startswith("AKSARAY/MERKEZ") else c[0].split()[0]
            kind = "mahalle" if "MAHALLE" in c[0] else "köy"
        elif len(c) == 5 and c[0].isdigit() and ilce:
            belde = "" if fold(c[1]) in (fold(ilce), "MERKEZ") else c[1]
            rows.append(line(c[0], ilce, belde, kind, c[2], c[3]))
    return rows


KIRIKKALE = (
    "https://www.kirikkale.gov.tr/kurumlar/kirikkale.gov.tr/site/site_files/il_idare_ve_denetim/"
    "ilGeneliMuhtarListesi_2026ekim.pdf"
)
Parser = Callable[..., list[dict[str, str]]]


class Province(NamedTuple):
    name: str
    publisher: str
    page: str
    title: str
    lists: dict[str, tuple[str, str, Parser]]
    method: str
    belde_ilce: bool = False


PROVINCES = {
    "03": Province(
        name="Afyonkarahisar",
        publisher="T.C. Afyonkarahisar Valiliği",
        page="https://www.afyonkarahisar.gov.tr/secilmisler-iletisim-bilgileri",
        title="Seçilmişler iletişim bilgileri: 2024 mahalle ve köy muhtarları listesi",
        lists={
            "mahalle": (
                f"{AFYON}/2024-Mahalle-Listesi.pdf",
                "accfa5371038ada158916848c1d1f6daca7dc3713b5ff1f1f0d7048858cffa19",
                afyon_pdf,
            ),
            "köy": (
                f"{AFYON}/2024_Koy_Listesi.pdf",
                "13e9ffcf6d2a24643890e00c8002a44d8a0563b72c6e2f050907bbeeca0f1604",
                afyon_pdf,
            ),
        },
        method=(
            "Afyonkarahisar's muhtars elected on 31 March 2024, as the governorate lists them on its page of elected "
            "officials: one list for the mahalles (dated May 2024, sha256 {mahalle}) and one for the köys (dated "
            "February 2026, sha256 {köy}). The lists also print phone numbers and, for köys, national identity "
            "numbers, so the archive keeps only a names-only extract of each and stores neither file. The köy list "
            "was compiled in 2026, so a köy whose muhtar changed after the election may list the successor instead "
            "of the 2024 winner."
        ),
    ),
    "41": Province(
        name="Kocaeli",
        publisher="T.C. Kocaeli Valiliği",
        page="https://www.kocaeli.gov.tr/kurumlar/kocaeli.gov.tr/Yeni_Site/Muhtar-Iletisim/Muhtarlar.pdf",
        title="Muhtar iletişim bilgileri",
        lists={
            "all": (
                "https://www.kocaeli.gov.tr/kurumlar/kocaeli.gov.tr/Yeni_Site/Muhtar-Iletisim/Muhtarlar.pdf",
                "fae2d6838191213e54e7858f165b6019b90afafcbbb9d2a66cd1209fbb6d49b8",
                kocaeli_pdf,
            )
        },
        method=(
            "Kocaeli's mahalle muhtars as the governorate lists them in its muhtar contact PDF (created October 2026, "
            "sha256 {all}). The PDF also prints phone numbers, so the archive keeps only a names-only extract and "
            "stores neither the file nor the numbers. " + UNDATED
        ),
    ),
    "45": Province(
        name="Manisa",
        publisher="T.C. Manisa Valiliği",
        page="https://www.manisa.gov.tr/kurumlar/manisa.gov.tr/documents/muhtar/muhtar_iletisim_bilgileri.pdf",
        title="Manisa İli Mahalle Muhtarları İletişim Bilgileri",
        lists={
            "all": (
                "https://www.manisa.gov.tr/kurumlar/manisa.gov.tr/documents/muhtar/muhtar_iletisim_bilgileri.pdf",
                "746da9b732a61fa112841dfeac0a15657868d85bc074fddd61d0ca531e7b1e78",
                manisa_pdf,
            )
        },
        method=(
            "Manisa's mahalle muhtars as the governorate lists them in its muhtar contact PDF (created October 2026, "
            "sha256 {all}). The PDF also prints phone numbers, so the archive keeps only a names-only extract and "
            "stores neither the file nor the numbers. " + UNDATED
        ),
    ),
    "71": Province(
        name="Kırıkkale",
        publisher="T.C. Kırıkkale Valiliği",
        page="https://www.kirikkale.gov.tr/idare-ve-denetim-mudurlugu",
        title="31 Mart 2024 tarihi itibariyle seçilen köy ve mahalle muhtarlarının listesi",
        lists={
            "all": (
                KIRIKKALE,
                "aaa6503f80d3fd2368cf03c052b40a6152a1c760a6a5fed400700762a8ad1c05",
                kirikkale_pdf,
            )
        },
        method=(
            "Kırıkkale's köy and mahalle muhtars as the governorate lists them in its province-wide PDF, titled as "
            "the muhtars elected as of 31 March 2024 (file name dated October 2026, sha256 {all}). The PDF also "
            "prints phone numbers, so the archive keeps only a names-only extract and stores neither the file nor "
            "the numbers. The file was compiled in 2026, so a muhtar who took office after a by-election may be "
            "listed instead of the 2024 winner."
        ),
    ),
    "81": Province(
        name="Düzce",
        publisher="T.C. Düzce Valiliği",
        page="https://www.duzce.gov.tr/mutharliklar",
        title="Muhtarlıklar: köy ve mahalle muhtar listesi",
        lists={
            "all": (
                "https://www.duzce.gov.tr/mutharliklar",
                "3ddc5237e890b4ec2b1a624249a74962fe9fc76d46dc7cd83a7d1dc551893e6a",
                duzce_html,
            )
        },
        method=(
            "Düzce's köy and mahalle muhtars as the governorate lists them on its muhtarlıklar page, one table per "
            "ilçe (names-only extract, sha256 {all}). The page also prints phone numbers, so the archive keeps only "
            "the names and stores neither the page nor the numbers. " + UNDATED
        ),
    ),
    "70": Province(
        name="Karaman",
        publisher="T.C. Karaman Valiliği",
        page="https://www.karaman.gov.tr/il-mahalli-idareler-mudurlugu",
        title="İl Mahalli İdareler Müdürlüğü: 2024 köy ve mahalle muhtarları",
        lists={
            "all": (
                "https://www.karaman.gov.tr/il-mahalli-idareler-mudurlugu",
                "7f65a2587c565b87ce4544edde566120c71f4e2c8a001d56cb388248cfc379b3",
                karaman_html,
            )
        },
        method=(
            "Karaman's köy and mahalle muhtars as the governorate lists them in the table on its local administration "
            "directorate page, headed 2024 (names-only extract, sha256 {all}). The page also prints phone numbers, "
            "so the archive keeps only the names and stores neither the page nor the numbers. The page was read in "
            "2026, so a muhtar who took office after a by-election may be listed instead of the 2024 winner."
        ),
        belde_ilce=True,
    ),
    "68": Province(
        name="Aksaray",
        publisher="T.C. Aksaray Valiliği",
        page="https://www.aksaray.gov.tr/koy-muhtarlari-iletisim-bilgileri",
        title="Aksaray İl Geneli Köy ve Mahalle Muhtarlığı Listesi (2024)",
        lists={
            "all": (
                "https://www.aksaray.gov.tr/koy-muhtarlari-iletisim-bilgileri",
                "3219195790341f67a4abebd9d45d20d7b98e133ce7a80afb1dad0199ed7a8be8",
                aksaray_html,
            )
        },
        method=(
            "Aksaray's köy and mahalle muhtars as the governorate lists them on its page headed 2024, one table per "
            "ilçe (names-only extract, sha256 {all}). The page also prints phone numbers, so the archive keeps only "
            "the names and stores neither the page nor the numbers. The page was read in 2026, so a muhtar who took "
            "office after a by-election may be listed instead of the 2024 winner."
        ),
    ),
}


def extract(url: str, kind: str, parse: Parser, content: bytes) -> bytes:
    if url.endswith(".pdf"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            pdf.write(content)
            pdf.flush()
            rows = parse(Path(pdf.name), kind)
    else:
        rows = parse(content.decode("utf-8"), kind)
    for row in rows:
        if re.search(r"VEKIL|VEKALET", fold(row["name"] + row["first"])):
            print(f"left out: {url.rsplit('/', 1)[-1]} {row['ilce']} {row['name']} {row['first']}")
    rows = [row for row in rows if not re.search(r"VEKIL|VEKALET", fold(row["name"] + row["first"]))]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, COLUMNS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def fetch() -> None:
    for config in PROVINCES.values():
        for kind, (url, digest, parse) in config.lists.items():
            request = urllib.request.Request(url, headers={"User-Agent": BROWSER})
            content = urllib.request.urlopen(request, timeout=120).read()
            names = extract(url, kind, parse, content)
            seen = hashlib.sha256(content if url.endswith(".pdf") else names).hexdigest()
            if seen != digest:
                raise ValueError(f"the {config.name} {kind} list changed since {digest}; reread it, now {seen}")
            raw.store(
                SOURCE,
                f"{url}#names",
                names,
                suffix=".tsv",
                retrieved_at=datetime.now(UTC),
                media_type="text/tab-separated-values",
            )
            time.sleep(1)


def join(
    register: list[dict[str, str]], prefix: str, listed: list[dict[str, str]], belde_ilce: bool
) -> dict[int, dict[str, str]]:
    pool: dict[tuple[str, str], list[tuple[str, str, dict[str, str]]]] = {}
    beldes: dict[tuple[str, str], list[tuple[str, str, dict[str, str]]]] = {}
    for row in register:
        ilce = row["ilce"].partition(" ")
        ilce = ilce[2] if fold(ilce[0]) == fold(prefix) and ilce[2] else row["ilce"]
        kind = "köy" if row["kind"] == "köy" else "mahalle"
        item = (fold(row["belde"]), stem(row["name"], row["belde"]), row)
        pool.setdefault((fold(ilce), kind), []).append(item)
        if row["belde"]:
            beldes.setdefault((fold(row["belde"]), kind), []).append(item)
    joined: dict[int, dict[str, str]] = {}
    used: set[int] = set()

    def near(entry: dict[str, str]) -> list[tuple[str, str, dict[str, str]]]:
        kinds = [entry["kind"]] if entry["kind"] else ["köy", "mahalle"]
        place = fold(entry["ilce"])
        if belde_ilce and not entry["belde"] and not any((place, kind) in pool for kind in kinds):
            items = [item for kind in kinds for item in beldes.get((place, kind), [])]
        else:
            items = [
                item
                for kind in kinds
                for item in pool.get((place, kind), [])
                if not (belde_ilce and not entry["belde"]) or not item[0]
            ]
            if entry["belde"]:
                items = [item for item in items if item[0] == fold(entry["belde"])]
        return [item for item in items if id(item[2]) not in used]

    def take(index: int, row: dict[str, str]) -> None:
        joined[index] = row
        used.add(id(row))

    for index, entry in enumerate(listed):
        found = [item for item in near(entry) if item[1] == stem(entry["name"], "")]
        if len(found) == 1:
            take(index, found[0][2])
    for index, entry in enumerate(listed):
        if index in joined:
            continue
        found = {re.sub(r"KOY$", "", item[1]): item[2] for item in near(entry)}
        close = difflib.get_close_matches(stem(entry["name"], ""), list(found), 2, 0.8)
        if len(close) == 1 or (
            close and difflib.SequenceMatcher(None, stem(entry["name"], ""), close[1]).ratio() < 0.8
        ):
            take(index, found[close[0]])
    left = [index for index in range(len(listed)) if index not in joined]
    holds = {
        index: [
            item
            for item in near(listed[index])
            if len(item[1]) >= 4
            and (item[1] in stem(listed[index]["name"], "") or stem(listed[index]["name"], "") in item[1])
        ]
        for index in left
    }
    for index, items in holds.items():
        if len(items) == 1 and sum(1 for other in holds.values() if items[0] in other) == 1:
            take(index, items[0][2])
    return joined


def emit() -> Path:
    with REGISTER.open(encoding="utf-8") as handle:
        everything = list(csv.DictReader(handle, delimiter="\t"))
    plans = []
    for plate, config in PROVINCES.items():
        register = [row for row in everything if row["plate"] == plate]
        listed = []
        for kind, (url, digest, _) in config.lists.items():
            artifact = raw.cached(SOURCE, f"{url}#names")
            with artifact.path.open(encoding="utf-8") as handle:
                listed += [(kind, artifact, row) for row in csv.DictReader(handle, delimiter="\t")]
        joined = join(register, config.name, [row for _, _, row in listed], config.belde_ilce)
        if len({row["muhtarlik_id"] for row in joined.values()}) != len(joined):
            raise ValueError(f"two listed {config.name} muhtarlıks join one YSK code")
        for index, (kind, _, row) in enumerate(listed):
            if index not in joined:
                print(f"not joined: {config.name} {kind} {row['ilce']} {row['belde']} {row['name']}")
        used = {id(row) for row in joined.values()}
        for row in register:
            if id(row) not in used:
                print(f"not listed: {row['muhtarlik_id']} {row['ilce']} {row['belde']} {row['name']}")
        print(f"{config.name}: {len(joined)} contests of {len(listed)} listed and {len(register)} YSK muhtarlıks")
        plans.append((config, listed, joined))

    codes = [row["muhtarlik_id"] for _, _, joined in plans for row in joined.values()]
    with connect() as conn:
        names = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'tr.mahalle' and c.code = any(%s)""",
                [codes],
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Muhtars elected on 31 March 2024, from the governorates' lists: winners only",
            polity="TR",
        )
        election = cs.key("tr.election", f"{DAY}:local")
        if election in cs.minted:
            raise ValueError("publish tr.ysk_local before tr.muhtar_2024")
        office = cs.key("tr.office", "local-muhtars")
        if office in cs.minted:
            raise ValueError("publish tr.muhtar_1963 before tr.muhtar_2024")
        for config, listed, joined in plans:
            source = cs.source(config.publisher, config.title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=config.publisher,
                title=config.title,
                url=config.page,
                license="Official publication, no license stated",
            )
            artifacts = {kind: cs.artifact(artifact, source) for kind, artifact, _ in listed}
            method = config.method.format(**{kind: digest for kind, (_, digest, _) in config.lists.items()}) + TAIL
            for index, row in joined.items():
                kind, _, entry = listed[index]
                code = row["muhtarlik_id"]
                winner = f"{entry['first']} {entry['last']}".strip()
                contest = cs.key("tr.contest", f"{DAY}:muhtar:{code}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=cs.area("tr.mahalle", code, DAY),
                    magnitude=1,
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"Muhtar of {names[code]}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{names[code]} Muhtarı",
                    lang="tr",
                    kind="native",
                    is_preferred=False,
                )
                declared = cs.key(f"{SOURCE}.result_set", code)
                cs.add(
                    "result_set",
                    id=declared,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=0,
                )
                cs.add("note", entity_id=declared, kind="methodology", body_md=method)
                cs.add(
                    "citation",
                    entity_id=declared,
                    source_id=source,
                    locator={"artifact": str(artifacts[kind]), "list": kind, "row": int(entry["no"])},
                )
                person = cs.key(f"{SOURCE}.person", code)
                cs.add("person", id=person)
                cs.add("name", entity_id=person, text=winner, lang="tr", kind="official", is_preferred=True)
                option = cs.key("tr.option", f"{DAY}:muhtar:{code}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=winner)
                candidacy = cs.key("tr.candidacy", f"{DAY}:muhtar:{code}")
                cs.add("candidacy", id=candidacy, person_id=person, option_id=option, role="head", is_independent=False)
                cs.add(
                    "outcome", result_set_id=declared, option_id=option, candidacy_id=None, kind="elected", seats=None
                )
        return cs.write()
