import re
import subprocess
from collections import Counter
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.tr.ysk import PROVINCES
from ea.sources.tr.tbmm import capitalize, fold, title
from ea.store import DATA, connect

SOURCE = "tr.tbmm_album"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://cdn.tbmm.gov.tr/TbmmWeb/Albumler/Tbmm_Albumu/Cilt2.pdf"
TITLE = "TBMM Albümü 1920-2010, 2. Cilt (1950-1980)"
PAGES = {12: (247, 312), 13: (313, 376), 14: (377, 440), 15: (441, 504), 16: (505, 568)}
LABELS = {12: "I. (XII)", 13: "II. (XIII)", 14: "III. (XIV)", 15: "IV. (XV)", 16: "V. (XVI)"}
OPENINGS = {
    12: date(1961, 10, 25),
    13: date(1965, 10, 22),
    14: date(1969, 10, 22),
    15: date(1973, 10, 24),
    16: date(1977, 6, 13),
}
CLOSED = date(1980, 9, 12)
PARTIES = {
    "AP": "ADALET PARTİSİ",
    "MP": "MİLLET PARTİSİ",
    "CHP": "CUMHURİYET HALK PARTİSİ",
    "CKMP": "CUMHURİYETÇİ KÖYLÜ MİLLET PARTİSİ",
    "MHP": "MİLLİYETÇİ HAREKET PARTİSİ",
    "YTP": "YENİ TÜRKİYE PARTİSİ",
    "MGP": "MİLLİ GÜVEN PARTİSİ",
    "TBP": "TÜRKİYE BİRLİK PARTİSİ",
    "BAP": "BÜYÜK ANADOLU PARTİSİ",
    "DP": "DEMOKRATİK PARTİ",
    "HP": "HÜRRİYET PARTİSİ",
    "YDP": "YENİ DEMOKRAT PARTİ",
    "CMP": "CUMHURİYETÇİ MİLLET PARTİSİ",
    "TKP": "TÜRKİYE KÖYLÜ PARTİSİ",
    "GP": "GÜVEN PARTİSİ",
    "BP": "BİRLİK PARTİSİ",
    "MNP": "MİLLİ NİZAM PARTİSİ",
    "TİP": "TÜRKİYE İŞÇİ PARTİSİ",
    "CGP": "CUMHURİYETÇİ GÜVEN PARTİSİ",
    "MSP": "MİLLİ SELAMET PARTİSİ",
}
HEADING = re.compile(r"^ {0,3}([A-ZÇĞİÖŞÜÂÎÛ][A-ZÇĞİÖŞÜÂÎÛ.' -]+)$")
PARTY_LINE = re.compile(r"^[A-ZÇĞİÖŞÜ/ –-]+$")
DEATH = re.compile(r"Ölüm Tarihi: (\d\d)\.(\d\d)\.(\d{4})")
BIRTH = re.compile(r"^(.+?) [–-] (\d{4}),")
KNOWN = {fold(name) for name in [*PROVINCES, "İçel", "Urfa", "Maraş"]}
RUNNING = re.compile(r"YASAMA DÖNEMİ /|^\d{3,4}$|^TBMM Albümü$")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    pdf = raw.cached(SOURCE, URL).path
    rows = []
    for term, (first, last) in PAGES.items():
        current: dict[str, object] | None = None
        done = False
        for page in range(first, last + 1):
            for column in (0, 1):
                text = subprocess.run(
                    ["pdftotext", "-f", str(page), "-l", str(page), "-layout", "-x", str(column * 305), "-y", "0",
                     "-W", "305", "-H", "850", str(pdf), "-"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                for line in text.replace("\xad", "").splitlines():
                    stripped = line.strip()
                    if "DİPNOTLAR" in stripped:
                        done = True
                    if done or not stripped or RUNNING.search(stripped):
                        continue
                    if HEADING.match(line) and fold(stripped) in KNOWN:
                        if current is not None:
                            rows.append(current)
                        current = {"term": term, "page": page, "province": stripped, "lines": []}
                        continue
                    if current is not None and not re.fullmatch(r"\d{1,2}", stripped):
                        current["lines"].append(stripped)
        if current is not None:
            rows.append(current)
    entries = []
    for row in rows:
        lines = row["lines"]
        name = lines[0]
        index = 1
        if name.count("(") > name.count(")"):
            while name.count("(") > name.count(")"):
                name += " " + lines[index]
                index += 1
        elif index < len(lines) and lines[index].startswith("("):
            while True:
                name += " " + lines[index]
                index += 1
                if name.count("(") == name.count(")"):
                    break
        party = ""
        if index < len(lines) and PARTY_LINE.match(lines[index]):
            party = lines[index]
            index += 1
        bio = ""
        for line in lines[index:]:
            bio = bio + line if bio.endswith("-") and not bio.endswith(" -") else f"{bio} {line}".strip()
        bio = re.sub(r"\s+", " ", bio)
        listed = re.match(r"^(.*?)\s*(?:\((.*)\))?$", name)
        assert listed is not None
        birth = BIRTH.match(bio)
        death = DEATH.search(bio)
        label = LABELS[row["term"]]
        entries.append(
            {
                "term": row["term"],
                "page": row["page"],
                "province": row["province"],
                "name": listed.group(1).strip(),
                "registered": listed.group(2),
                "parties": [token for token in re.split(r"\s*[/–-]\s*", party) if token],
                "birthplace": None if birth is None else birth.group(1),
                "born": None if birth is None else int(birth.group(2)),
                "died": None if death is None else f"{death.group(3)}-{death.group(2)}-{death.group(1)}",
                "by_election": re.search(rf"{re.escape(label)}(?: Dönem)? ?\(Ara Seçim\)", bio) is not None,
                "bio": bio,
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(entries).write_parquet(PARSED / "entries.parquet")
    print(Counter(entry["term"] for entry in entries))


def emit() -> Path:
    entries = pl.read_parquet(PARSED / "entries.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Members of the National Assembly (Millet Meclisi), terms XII to XVI (1961-1980), from the TBMM Albümü",
            polity="TR",
        )
        source = cs.source("Türkiye Büyük Millet Meclisi", TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Türkiye Büyük Millet Meclisi",
            title=TITLE,
            url=URL,
            license="Official public record, no license stated",
            notes=(
                "Biographies of every member of each legislative term, in a column per province with the party "
                "or parties the member sat under (in order, undated), the birthplace and year, and the date of "
                "death. It marks members who entered at a by-election but gives no start or end dates, so a "
                "tenure runs from the term's opening to the next term's opening and ends early only at a death."
            ),
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        body = conn.execute(
            """select b.id from body b join name n on n.entity_id = b.id
               where n.text = 'Millet Meclisi' and b.polity_id = (select id from polity where iso_code = 'TR')"""
        ).fetchone()
        assert body is not None
        parties = {
            fold(text): party
            for party, text in conn.execute(
                """select distinct o.party_id, n.text from contest_option o
                   join contest c on c.id = o.contest_id join election e on e.id = c.election_id
                   join name n on n.entity_id = o.party_id and n.lang in ('tr', 'en') and n.kind <> 'abbreviation'
                   join polity p on p.id = e.polity_id
                   where p.iso_code = 'TR' and e.date_from between '1961-01-01' and '1977-12-31' and o.party_id is not null"""
            )
        }
        counts: Counter[str] = Counter()
        written: set[object] = set()
        for row in entries.iter_rows(named=True):
            term = row["term"]
            person = cs.key(
                f"{SOURCE}.person",
                f"{fold(row['registered'] or row['name'])}:{fold(row['birthplace'] or '')}:{row['born']}",
            )
            if person in cs.minted and person not in written:
                written.add(person)
                died = None if row["died"] is None else date.fromisoformat(row["died"])
                cs.add(
                    "person",
                    id=person,
                    birth_date=None if row["born"] is None else date(row["born"], 1, 1),
                    birth_precision="year",
                    death_date=died,
                    death_precision="day",
                )
                for text, preferred in ((row["name"], True), (row["registered"], False)):
                    if text is not None:
                        cs.add(
                            "name",
                            entity_id=person,
                            text=title(text),
                            lang="tr",
                            kind="official",
                            is_preferred=preferred,
                        )
            ended = None if row["died"] is None else date.fromisoformat(row["died"])
            closing = OPENINGS.get(term + 1, CLOSED)
            died = ended is not None and OPENINGS[term] < ended < closing
            tenure = cs.key(f"{SOURCE}.tenure", f"{term}:{person}")
            cs.add(
                "tenure",
                id=tenure,
                person_id=person,
                body_id=body[0],
                from_date=OPENINGS[term],
                from_precision="unknown",
                to_date=ended if died else closing,
                acquired_via="elected",
                ended_via="died" if died else "dissolved" if term == 16 else "term_end",
            )
            cs.add("citation", entity_id=tenure, source_id=source, locator={"term": term, "page": row["page"]})
            cs.add(
                "note",
                entity_id=tenure,
                kind="context",
                body_md=f"Member for {capitalize(row['province'])}"
                + (", entering at a by-election." if row["by_election"] else "."),
            )
            if died:
                cs.add("tenure_event", tenure_id=tenure, kind="died", date=ended, source_id=source)
            counts[term, "by_election" if row["by_election"] else "general"] += 1
            if len(row["parties"]) == 1 and row["parties"][0] in PARTIES:
                party = parties.get(fold(PARTIES[row["parties"][0]]))
                if party is not None:
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=party,
                        valid_from=OPENINGS[term],
                        valid_from_precision="unknown",
                        valid_to=ended if died else closing,
                    )
                    counts[term, "party"] += 1
        for key, count in sorted(counts.items()):
            print(key, count)
        return cs.write()
