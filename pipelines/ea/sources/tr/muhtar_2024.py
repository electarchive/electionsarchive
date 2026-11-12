import csv
import difflib
import hashlib
import io
import re
import subprocess
import tempfile
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "tr.muhtar_2024"
VERSION = "1"
DAY = date(2024, 3, 31)
REGISTER = Path(__file__).with_name("muhtarlik_2024.tsv")
PROVINCE = "Afyonkarahisar"
FOLDER = "https://www.afyonkarahisar.gov.tr/kurumlar/afyonkarahisar.gov.tr/Dosyalar/" + quote("Seçilmişler")
LISTS = {
    "mahalle": (
        f"{FOLDER}/2024-Mahalle-Listesi.pdf",
        "accfa5371038ada158916848c1d1f6daca7dc3713b5ff1f1f0d7048858cffa19",
    ),
    "köy": (f"{FOLDER}/2024_Koy_Listesi.pdf", "13e9ffcf6d2a24643890e00c8002a44d8a0563b72c6e2f050907bbeeca0f1604"),
}
PAGE = "https://www.afyonkarahisar.gov.tr/secilmisler-iletisim-bilgileri"
PUBLISHER = "T.C. Afyonkarahisar Valiliği"
TITLE = "Seçilmişler iletişim bilgileri: 2024 mahalle ve köy muhtarları listesi"
COLUMNS = ("no", "ilce", "belde", "kind", "name", "first", "last")
ROW = re.compile(r"^\s*(\d+)\s+AFYONKARAH")
METHOD = (
    "Afyonkarahisar's muhtars elected on 31 March 2024, as the governorate lists them on its page of elected officials: "
    "one list for the mahalles (dated May 2024, sha256 {mahalle}) and one for the köys (dated February 2026, sha256 "
    "{koy}). The lists also print phone numbers and, for köys, national identity numbers, so the archive keeps only a "
    "names-only extract of each and stores neither file. Each listed muhtarlık is joined to its YSK muhtarlık code by "
    "ilçe, belde, kind, and name; a spelling variant is joined only when it is the one close match left in its ilçe "
    "and belde. YSK publishes no muhtar vote counts, so each contest carries the winner only. The köy list was "
    "compiled in 2026, so a köy whose muhtar changed after the election may list the successor instead of the 2024 "
    "winner. Candidates for muhtar stand without party labels."
)


def fold(text: str) -> str:
    text = text.replace("İ", "I").upper().translate(str.maketrans("ŞĞÜÖÇÂÎÛ", "SGUOCAIU"))
    return re.sub(r"[^A-Z0-9]", "", text)


def stem(name: str, belde: str) -> str:
    text = re.sub(r"\s*\(.*?\)", "", name.strip())
    text = re.sub(r"(\s+MAH\.?|\s*MAHALLESİ|MAHALLE|\s+KÖYÜ|\s+KÖY)$", "", text)
    if belde:
        text = re.sub(r"^" + re.escape(belde) + r"\s*-\s*", "", text)
    return fold(text)


def extract(pdf: Path, kind: str) -> list[dict[str, str]]:
    lines = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    rows = []
    for index, line in enumerate(lines):
        if not ROW.match(line):
            continue
        fields = re.split(r"\s{2,}", line.strip())
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


def fetch() -> None:
    for kind, (url, digest) in LISTS.items():
        request = urllib.request.Request(url, headers={"User-Agent": raw.USER_AGENT})
        content = urllib.request.urlopen(request, timeout=120).read()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"the {kind} list changed since {digest}; reread it before storing a new extract")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            pdf.write(content)
            pdf.flush()
            rows = extract(Path(pdf.name), kind)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        raw.store(
            SOURCE,
            f"{url}#names",
            buffer.getvalue().encode(),
            suffix=".tsv",
            retrieved_at=datetime.now(UTC),
            media_type="text/tab-separated-values",
        )


def emit() -> Path:
    with REGISTER.open(encoding="utf-8") as handle:
        register = [row for row in csv.DictReader(handle, delimiter="\t") if row["plate"] == "03"]
    codes = {}
    for row in register:
        ilce = re.sub(r"^AFYONKARAHİSAR ", "", row["ilce"])
        kind = "köy" if row["kind"] == "köy" else "mahalle"
        codes[fold(ilce), fold(row["belde"]), kind, stem(row["name"], row["belde"])] = row
    listed = []
    for kind, (url, _) in LISTS.items():
        artifact = raw.cached(SOURCE, f"{url}#names")
        with artifact.path.open(encoding="utf-8") as handle:
            listed += [(kind, artifact, row) for row in csv.DictReader(handle, delimiter="\t")]
    keys = [(fold(row["ilce"]), fold(row["belde"]), kind, stem(row["name"], "")) for kind, _, row in listed]
    joined: dict[int, dict[str, str]] = {index: codes[key] for index, key in enumerate(keys) if key in codes}
    used = {id(row) for row in joined.values()}
    for index, key in enumerate(keys):
        if index in joined:
            continue
        pool = {
            re.sub(r"KOY$", "", other[3]): row
            for other, row in codes.items()
            if other[:3] == key[:3] and id(row) not in used
        }
        close = difflib.get_close_matches(key[3], list(pool), 2, 0.8)
        if len(close) == 1 or (close and difflib.SequenceMatcher(None, key[3], close[1]).ratio() < 0.8):
            joined[index] = pool[close[0]]
            used.add(id(joined[index]))
    if len({row["muhtarlik_id"] for row in joined.values()}) != len(joined):
        raise ValueError("two listed muhtarlıks join one YSK code")
    for index, (kind, _, row) in enumerate(listed):
        if index not in joined:
            print(f"not joined: {kind} {row['ilce']} {row['belde']} {row['name']}")
    for row in register:
        if id(row) not in used:
            print(f"not listed: {row['muhtarlik_id']} {row['ilce']} {row['belde']} {row['name']}")

    with connect() as conn:
        names = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'tr.mahalle' and c.code = any(%s)""",
                [[row["muhtarlik_id"] for row in joined.values()]],
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Muhtars elected on 31 March 2024 in {PROVINCE}, from the governorate's lists: winners only",
            polity="TR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Official publication, no license stated",
        )
        artifacts = {kind: cs.artifact(artifact, source) for kind, artifact, _ in listed}
        election = cs.key("tr.election", f"{DAY}:local")
        if election in cs.minted:
            raise ValueError("publish tr.ysk_local before tr.muhtar_2024")
        office = cs.key("tr.office", "local-muhtars")
        if office in cs.minted:
            raise ValueError("publish tr.muhtar_1963 before tr.muhtar_2024")
        method = METHOD.format(mahalle=LISTS["mahalle"][1], koy=LISTS["köy"][1])
        for index, row in joined.items():
            kind, _, entry = listed[index]
            code = row["muhtarlik_id"]
            winner = f"{entry['first']} {entry['last']}"
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
                "name", entity_id=contest, text=f"{names[code]} Muhtarı", lang="tr", kind="native", is_preferred=False
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
            cs.add("outcome", result_set_id=declared, option_id=option, candidacy_id=None, kind="elected", seats=None)
        print(f"{len(joined)} contests of {len(listed)} listed and {len(register)} YSK muhtarlıks")
        return cs.write()
