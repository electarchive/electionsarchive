import re
import subprocess
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.tbmm import capitalize, fold, title
from ea.sources.tr.ysk import PROVINCES
from ea.store import DATA, connect

SOURCE = "tr.tbmm_1920"
VERSION = "2"
TITLE_ALBUM = "TBMM Albümü 1920-2010, 1. Cilt (1920-1950)"
TITLE_TUIK = "Milletvekili Genel Seçimleri, 1923-2011"
PARSED = DATA / "parsed" / SOURCE
ALBUM = "https://cdn.tbmm.gov.tr/TbmmWeb/Albumler/Tbmm_Albumu/Cilt1.pdf"
TUIK = "https://www.ysk.gov.tr/doc/dosyalar/1923-2011-MVSecimleri-Tuik.pdf"
PAGES = {
    1: (33, 96),
    2: (105, 154),
    3: (157, 206),
    4: (209, 260),
    5: (263, 326),
    6: (329, 394),
    7: (397, 466),
    8: (471, 538),
}
LABELS = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}
OLD = {
    "seyhan": "adana",
    "karahisarisahip": "afyonkarahisar",
    "bayazit": "agri",
    "ayintab": "gaziantep",
    "gaziayintab": "gaziantep",
    "karesi": "balikesir",
    "ertugrul": "bilecik",
    "genc": "bingol",
    "biga": "canakkale",
    "diyarbekir": "diyarbakir",
    "elaziz": "elazig",
    "icel": "mersin",
    "kangiri": "cankiri",
    "saruhan": "manisa",
    "mentese": "mugla",
    "canik": "samsun",
    "dersim": "tunceli",
    "bozok": "yozgat",
    "izmit": "kocaeli",
    "lazistan": "rize",
    "maras": "kahramanmaras",
    "urfa": "sanliurfa",
    "afyon": "afyonkarahisar",
    "karahisarisarki": "sebinkarahisar",
}
HISTORICAL = {
    "aksaray": "Aksaray",
    "ardahan": "Ardahan",
    "batum": "Batum",
    "cebelibereket": "Cebelibereket",
    "catalca": "Çatalca",
    "ergani": "Ergani",
    "gelibolu": "Gelibolu",
    "kozan": "Kozan",
    "mersinold": "Mersin district",
    "oltu": "Oltu",
    "sebinkarahisar": "Şebinkarahisar",
    "siverek": "Siverek",
}
OPENINGS = {
    1: date(1920, 4, 23),
    2: date(1923, 8, 11),
    3: date(1927, 11, 1),
    4: date(1931, 5, 4),
    5: date(1935, 3, 1),
    6: date(1939, 4, 3),
    7: date(1943, 3, 8),
    8: date(1946, 8, 5),
}
CLOSED = date(1950, 5, 22)
ELECTED = {
    2: date(1923, 6, 1),
    3: date(1927, 9, 2),
    4: date(1931, 4, 25),
    5: date(1935, 2, 8),
    6: date(1939, 3, 26),
    7: date(1943, 2, 28),
    8: date(1946, 7, 21),
}
YEARS = {2: 1923, 3: 1927, 4: 1931, 5: 1935, 6: 1939, 7: 1943}
SIZES = {2: 287, 3: 316, 4: 317, 5: 399, 6: 429, 7: 455, 8: 465}
PLATES = {fold(name): plate for plate, name in enumerate(PROVINCES, 1)}
HEADINGS = {
    "adana",
    "seyhan",
    "afyonkarahisar",
    "karahisarisahip",
    "agri",
    "bayazit",
    "ayintab",
    "gaziayintab",
    "gaziantep",
    "balikesir",
    "karesi",
    "bilecik",
    "ertugrul",
    "bingol",
    "genc",
    "canakkale",
    "biga",
    "diyarbekir",
    "diyarbakir",
    "elaziz",
    "elazig",
    "icel",
    "kangiri",
    "cankiri",
    "manisa",
    "saruhan",
    "mugla",
    "mentese",
    "rize",
    "coruh",
    "samsun",
    "canik",
    "tunceli",
    "dersim",
    "urfa",
    "yozgat",
    "bozok",
    "izmit",
    "kocaeli",
    "lazistan",
    "karahisarisarki",
    "sebinkarahisar",
    "mersin",
    "maras",
    "aksaray",
    "amasya",
    "ankara",
    "antalya",
    "ardahan",
    "artvin",
    "aydin",
    "batum",
    "bitlis",
    "bolu",
    "burdur",
    "bursa",
    "cebelibereket",
    "catalca",
    "corum",
    "denizli",
    "edirne",
    "ergani",
    "erzincan",
    "erzurum",
    "eskisehir",
    "gelibolu",
    "giresun",
    "gumushane",
    "hakkari",
    "hatay",
    "isparta",
    "istanbul",
    "izmir",
    "kars",
    "kastamonu",
    "kayseri",
    "kirklareli",
    "kirsehir",
    "konya",
    "kozan",
    "kutahya",
    "malatya",
    "mardin",
    "mus",
    "nigde",
    "oltu",
    "ordu",
    "siirt",
    "sinop",
    "sivas",
    "siverek",
    "tekirdag",
    "tokat",
    "trabzon",
    "van",
    "zonguldak",
}
HEADING = re.compile(r"^ {0,3}([A-ZÇĞİÖŞÜÂÎÛ][A-ZÇĞİÖŞÜÂÎÛ.' -]+)$")
PARTY_LINE = re.compile(r"^[A-ZÇĞİÖŞÜ/ –-]+$")
RUNNING = re.compile(r"YASAMA DÖNEMİ /|^\d{3,4}$|^TBMM Albümü$")
DEATH = re.compile(r"Ölüm Tarihi: (\d\d)\.(\d\d)\.(\d{4})")
BIRTH = re.compile(r"^(.+?) [–-] (\d{4}),")
RESIGNED = re.compile(
    r"[İi]stifa\s*:?\s*(\d\d)\.(\d\d)\.(\d{4})|(\d\d)\.(\d\d)\.(\d{4})\s*(?:tarihinde|’d[ae]|’t[ae]|d[ae])\s*(?:Milletvekilliğinden |Meclise katılmadan |Meclis’e katılmadan )?(?:istifa|[İi]stifası)"
)


def canon(text: str, term: int | None = None) -> str:
    key = fold(re.sub(r"\s*\(.*\)", "", text))
    if key == "coruh":
        return "rize" if term is not None and term <= 5 and term > 1 else "artvin"
    if key == "bitlis" and term == 3:
        return "mus"
    if key == "mersin":
        return "mersinold"
    if key == "icel":
        return "mersin"
    return key if key in HISTORICAL else OLD.get(key, key)


STOP = {"bey", "pasa", "efendi", "paşa", "avukat", "dr", "hoca", "kitapci"}


def words(text: str) -> set[str]:
    return {fold(word) for word in re.findall(r"[^\W\d_]+", text) if len(word) > 3 and fold(word) not in STOP}


def clause(bio: str, term: int, province: str) -> str:
    label = LABELS[term]
    found = []
    for segment in re.split(r"\s[–-]\s", bio):
        if "Milletvekili" not in segment or "Dönem" not in segment or "Meb" in segment.split("Milletvekili")[0][-60:]:
            continue
        for part in re.split(r",\s*|\s+ve\s+(?=[IVX]+\.\s*Dönem)", segment):
            head = re.split(r"Dönem", part, maxsplit=1)
            if len(head) == 2 and label in re.findall(r"\b([IVX]+)\.", head[0] + "."):
                found.append((part, segment))
    if not found:
        return bio
    here = [
        part
        for part, _ in found
        if canon(
            re.sub(r"^.*?Dönem", "", part).replace("(Ara Seçim)", "").strip().split(" Milletvekili")[0].split("(")[0],
            term,
        )
        == province
    ]
    chosen = here[0] if here else found[-1][0]
    tail = found[-1][1][found[-1][1].index(chosen) + len(chosen) :] if chosen == found[-1][0] else ""
    return chosen + " " + tail


def fetch() -> None:
    raw.fetch(SOURCE, ALBUM, suffix=".pdf")
    response = httpx.get(TUIK, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=120)
    response.raise_for_status()
    raw.store(
        SOURCE, TUIK, response.content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
    )


def column(pdf: Path, page: int, side: int) -> list[str]:
    text = subprocess.run(
        [
            "pdftotext",
            "-f",
            str(page),
            "-l",
            str(page),
            "-layout",
            "-x",
            str(side * 305),
            "-y",
            "0",
            "-W",
            "305",
            "-H",
            "850",
            str(pdf),
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return text.replace("\xad", "").splitlines()


def parse() -> None:
    pdf = raw.cached(SOURCE, ALBUM).path
    rows = []
    for term, (first, last) in PAGES.items():
        current: dict[str, object] | None = None
        done = False
        for page in range(first, last + 1):
            for side in (0, 1):
                for line in column(pdf, page, side):
                    stripped = line.strip()
                    if "DİPNOTLAR" in stripped:
                        done = True
                    if done or not stripped or RUNNING.search(stripped):
                        continue
                    if HEADING.match(line) and fold(stripped) in HEADINGS:
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
        while index < len(lines) and (
            name.count("(") > name.count(")")
            or name.count("[") > name.count("]")
            or (lines[index][:1] in "([" and name.count("(") + name.count("[") == 0)
        ):
            name += " " + lines[index]
            index += 1
            if name.count("(") == name.count(")") and name.count("[") == name.count("]"):
                break
        party = ""
        if index < len(lines) and PARTY_LINE.match(lines[index]) and len(lines[index]) < 40:
            party = lines[index]
            index += 1
        bio = ""
        for line in lines[index:]:
            bio = bio + line if bio.endswith("-") and not bio.endswith(" -") else f"{bio} {line}".strip()
        bio = re.sub(r"\s+", " ", bio)
        listed = re.match(r"^(.*?)\s*(?:[(\[](.*)[)\]])?$", name)
        birth = BIRTH.match(bio)
        death = DEATH.search(bio)
        part = clause(bio, row["term"], canon(row["province"], row["term"]))
        resigned = RESIGNED.search(part)
        resign_day = None
        if resigned is not None:
            groups = [g for g in resigned.groups() if g is not None]
            resign_day = f"{groups[2]}-{groups[1]}-{groups[0]}"
        entries.append(
            {
                "term": row["term"],
                "page": row["page"],
                "province": row["province"],
                "name": re.sub(r"(?<=[^\d\s])\d{1,2}$", "", listed.group(1).strip()),
                "registered": listed.group(2),
                "party": party,
                "birthplace": None if birth is None else birth.group(1),
                "born": None if birth is None else int(birth.group(2)),
                "died": None if death is None else f"{death.group(3)}-{death.group(2)}-{death.group(1)}",
                "by_election": "Ara Seçim" in part,
                "resigned": resign_day,
                "never_joined": re.search(r"gelmeden|katılmadan|katılmadığı", part) is not None,
                "bio": bio,
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(entries)
    frame.write_parquet(PARSED / "entries.parquet")
    print(Counter(entry["term"] for entry in entries))
    print(
        frame.group_by("term")
        .agg(
            pl.col("by_election").sum(),
            pl.col("never_joined").sum(),
            pl.col("resigned").count(),
            (pl.col("party") != "").sum().alias("party"),
        )
        .sort("term")
    )


def tuik() -> tuple[dict[str, dict[int, int]], dict[str, tuple[int, int, int, int]]]:
    pdf = raw.cached(SOURCE, TUIK).path
    fix = str.maketrans({"÷": "ğ", "ø": "İ", "ú": "ş", "ù": "ş"})
    seats: dict[str, dict[int, int]] = {}
    for line in column_text(pdf, 25).splitlines():
        found = re.match(r"^\s+([^\d\s][^\d]*?)\s{2,}((?:[\d-]+\s+){5}[\d-]+)\s*$", line.translate(fix))
        if found is None:
            continue
        name = re.sub(r"\s*\(.*\)", "", found.group(1)).strip()
        cells = found.group(2).split()
        if name in ("Toplam", "İl"):
            continue
        seats[canon(name)] = {
            year: 0 if cell == "-" else int(cell)
            for year, cell in zip((1923, 1927, 1931, 1935, 1939, 1943), cells, strict=True)
        }
    won: dict[str, tuple[int, int, int, int]] = {}
    for line in column_text(pdf, 26).splitlines():
        for found in re.finditer(
            r"([^\d\s][^\d]*?)\s{2,}(\d+)\s+([\d-]+)\s+([\d-]+)\s+([\d-]+)(?=\s{2,}|\s*$)", line.translate(fix)
        ):
            name = re.sub(r"\s*\(.*\)", "", found.group(1)).strip()
            if name in ("Toplam", "İl"):
                continue
            cells = [0 if cell == "-" else int(cell) for cell in found.groups()[1:]]
            won[canon(name)] = (cells[0], cells[1], cells[2], cells[3])
    return seats, won


def column_text(pdf: Path, page: int) -> str:
    return subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


TWO_STAGE = (
    "From 1923 to 1943 the vote was indirect. Voters chose secondary electors, who then chose the deputies; "
    "the 1946 election was the first in which voters chose deputies directly, openly cast and secretly counted. "
    "The archive holds the declared outcome, the members elected, and the seats per province from the TÜİK table. "
    "It holds no elector-stage counts, since no official or scholarly table printing them has been found."
)
ALBUM_NOTE = (
    "Biographies of every member of each legislative term, in a column per province. It marks members who entered "
    "at a by-election but gives no date for the by-election, so those tenures carry the term's opening date at "
    "unknown precision. Parties are printed only from the 1943 term (and for the Terakkiperver members of 1924)."
)
TUIK_NOTE = (
    "Table 5 gives the seats per province for the one-party elections of 1923 to 1943, and Table 6 the seats won "
    "by the CHP, the DP, and independents per province in 1946. The PDF's text layer garbles Turkish letters, which "
    "the parser repairs."
)
COUNT_CAVEAT = (
    "The province's magnitude is TÜİK's seat count; the Albüm lists every person who held a seat in the term, "
    "including replacements, so its per-province counts can differ by one or two."
)


def emit() -> Path:
    entries = pl.read_parquet(PARSED / "entries.parquet")
    seats, won = tuik()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Grand National Assembly members 1920-1950 and the 1923-1946 general elections by province, from the TBMM Albümü and TÜİK",
            polity="TR",
        )

        def name(entity: UUID, text: str, lang: str = "tr", kind: str = "official", **valid: object) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official" and valid.get("valid_to") is None,
                valid_from=valid.get("valid_from"),
                valid_from_precision="day",
                valid_to=valid.get("valid_to"),
                valid_to_precision="day",
            )

        album = cs.source("Türkiye Büyük Millet Meclisi", TITLE_ALBUM)
        cs.add(
            "source",
            id=album,
            kind="official_body",
            publisher="Türkiye Büyük Millet Meclisi",
            title=TITLE_ALBUM,
            url=ALBUM,
            license="Official public record, no license stated",
            notes=ALBUM_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, ALBUM), album)
        tuik_source = cs.source("Türkiye İstatistik Kurumu", TITLE_TUIK)
        cs.add(
            "source",
            id=tuik_source,
            kind="official_body",
            publisher="Türkiye İstatistik Kurumu",
            title=TITLE_TUIK,
            url=TUIK,
            license="Official public record, no license stated",
            notes=TUIK_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, TUIK), tuik_source)

        turkey = cs.polity("TR")
        nation = cs.area("tr.nation", "TR")
        assembly = cs.key("tr.body", "tbmm")
        if assembly in cs.minted or turkey in cs.minted:
            raise ValueError("publish tr.ysk and tr.thea before tr.tbmm_1920")
        areas: dict[str, UUID] = {}
        labels: dict[str, str] = {}
        for plate, label in enumerate(PROVINCES, 1):
            areas[fold(label)] = cs.area("tr.il", f"{plate:02}")
            labels[fold(label)] = label
        for key, label in HISTORICAL.items():
            areas[key] = cs.area("tr.il", f"h-{key}")
            labels[key] = label
            if areas[key] in cs.minted:
                cs.add("area", id=areas[key], scheme_code="tr.il")
                cs.add("area_code", area_id=areas[key], code=f"h-{key}", valid_from=None)
                cs.add("area_parent", area_id=areas[key], parent_id=nation, valid_from=None)
                name(areas[key], label)
                cs.add(
                    "note",
                    entity_id=areas[key],
                    kind="context",
                    body_md="A province or electoral district of the 1920s and 1930s, as TBMM and TÜİK list it; it has no counterpart in today's 81 provinces and no boundary in the archive.",
                )

        for term, size in SIZES.items():
            cs.add(
                "body_size",
                body_id=assembly,
                seats=size,
                valid_from=OPENINGS[term],
                valid_from_precision="day",
                valid_to=OPENINGS.get(term + 1, date(1950, 5, 14)) if term < 8 else date(1950, 5, 14),
                valid_to_precision="day",
            )
        system_23 = cs.key("tr.electoral_system", "parliament_1923")
        cs.add("electoral_system", id=system_23, polity_id=turkey, rules={"formula": "block"}, rules_version=1)
        name(system_23, "Grand National Assembly two-stage election, 1923 to 1943", "en")
        cs.add("note", entity_id=system_23, kind="methodology", body_md=TWO_STAGE)
        system_46 = cs.key("tr.electoral_system", "parliament_1946")
        if system_46 in cs.minted:
            raise ValueError("publish tr.thea before tr.tbmm_1920")
        parties = {key: cs.key("tr.party", key) for key in ("cumhuriyet-halk-partisi", "demokrat-parti-1946")}
        chp, dp = parties["cumhuriyet-halk-partisi"], parties["demokrat-parti-1946"]
        if chp in cs.minted or dp in cs.minted:
            raise ValueError("publish tr.thea before tr.tbmm_1920")

        listed = lists()
        independents = Counter(
            (row["term"], canon(row["province"], row["term"]))
            for row in listed.filter(pl.col("kind") == "IND").iter_rows(named=True)
        )
        groups = {
            "TCF": (
                "terakkiperver-cumhuriyet-firkasi",
                "Terakkiperver Cumhuriyet Fırkası",
                "TCF",
                date(1924, 11, 17),
                date(1925, 6, 5),
            ),
            "SCF": (
                "serbest-cumhuriyet-firkasi",
                "Serbest Cumhuriyet Fırkası",
                "SCF",
                date(1930, 8, 18),
                date(1930, 11, 17),
            ),
        }
        group_parties = {}
        for code, (key, label, short, start, end) in groups.items():
            group_parties[code] = cs.key("tr.party", key)
            if group_parties[code] in cs.minted:
                cs.add("party", id=group_parties[code], kind="party", polity_id=turkey)
                name(group_parties[code], label)
                name(group_parties[code], short, kind="abbreviation")
        member_group: dict[tuple[int, str, str], tuple[str, str]] = {}
        for item in listed.filter(pl.col("kind") != "IND").iter_rows(named=True):
            wanted = words(item["name"])
            found = [
                (len(wanted & words(f"{row['name']} {row['registered'] or ''}")), row)
                for row in entries.filter(pl.col("term") == item["term"]).iter_rows(named=True)
                if canon(row["province"], item["term"]) == canon(item["province"], item["term"])
            ]
            best = max((score for score, _ in found), default=0)
            top = [row for score, row in found if score == best and best > 0]
            if len(top) == 1:
                member_group[item["term"], top[0]["page"], top[0]["name"]] = (item["kind"], item["name"])
            else:
                print("unmatched list member", item["kind"], item["name"], item["province"], len(top))
        contests: dict[tuple[int, str], UUID] = {}
        report = []
        for term, day in ELECTED.items():
            year = YEARS.get(term, 1946)
            path = f"tr/{day.year}-06-general" if term == 2 else f"tr/{day}-general"
            election = cs.key("tr.election", str(day))
            cs.add(
                "election",
                id=election,
                polity_id=turkey,
                kind="general",
                date_from=day,
                date_to=date(1923, 7, 31) if term == 2 else day,
                date_precision="month" if term == 2 else "day",
                status="held",
            )
            name(election, f"{year} Turkish general election", "en")
            cs.add("slug", path=path, entity_id=election)
            if term < 8:
                cs.add("note", entity_id=election, kind="caveat", body_md=TWO_STAGE)
            table = (
                {key: (won[key][0] if key in won else 0) for key in won}
                if term == 8
                else {key: values[year] for key, values in seats.items() if values[year] > 0}
            )
            counts = Counter(
                canon(row["province"], term)
                for row in entries.filter(
                    (pl.col("term") == term) & ~pl.col("by_election") & ~pl.col("never_joined")
                ).iter_rows(named=True)
            )
            for key, magnitude in sorted(table.items()):
                if key not in areas:
                    raise ValueError(f"no area for {key}")
                contest_key = f"{day}:parliament:{key}"
                contest = cs.key("tr.contest", contest_key)
                contests[term, key] = contest
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=assembly,
                    district_area_id=areas[key],
                    magnitude=magnitude,
                    round_number=1,
                    electoral_system_id=system_46 if term == 8 else system_23,
                    default_result_kind="counted",
                )
                name(contest, f"Grand National Assembly, {labels[key]}", "en")
                cs.add("slug", path=f"{path}/parliament/{slug(labels[key])}", entity_id=contest)
                result = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=tuik_source,
                    is_default=True,
                    completeness=0,
                )
                cs.add("note", entity_id=result, kind="caveat", body_md=COUNT_CAVEAT)
                if term == 8:
                    for label, party, seats_won in zip(
                        ("Cumhuriyet Halk Partisi", "Demokrat Parti", "Bağımsız"),
                        (chp, dp, None),
                        won[key][1:],
                        strict=True,
                    ):
                        if seats_won > 0:
                            option = cs.key("tr.option", f"{contest_key}:{slug(label)}")
                            cs.add(
                                "contest_option",
                                id=option,
                                contest_id=contest,
                                kind="list" if party is not None else "independents",
                                party_id=party,
                                ballot_label=label,
                            )
                            cs.add("outcome", result_set_id=result, option_id=option, kind="won", seats=seats_won)
                if independents.get((term, key)):
                    option = cs.key("tr.option", f"{contest_key}:bagimsiz")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="independents",
                        party_id=None,
                        ballot_label="Bağımsız",
                    )
                    cs.add("outcome", result_set_id=result, option_id=option, kind="won", seats=independents[term, key])
                if counts.get(key, 0) != magnitude:
                    report.append((year, key, magnitude, counts.get(key, 0)))
            for key in sorted(set(counts) - set(table)):
                report.append((year, key, 0, counts[key]))
        for line in report:
            print("count differs", line)

        digits = re.compile(r"\d+")
        written: set[UUID] = set()
        made = Counter()
        for row in entries.iter_rows(named=True):
            term = row["term"]
            key = canon(row["province"], term)
            if row["never_joined"]:
                made[term, "never joined"] += 1
                continue
            person = cs.key(
                "tr.tbmm_album.person",
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
                            text=title(digits.sub("", text).strip()),
                            lang="tr",
                            kind="official",
                            is_preferred=preferred,
                        )
            start = OPENINGS[term]
            closing = OPENINGS.get(term + 1, CLOSED)
            died = None if row["died"] is None else date.fromisoformat(row["died"])
            resigned = None if row["resigned"] is None else date.fromisoformat(row["resigned"])
            ended, via = closing, "dissolved" if term == 8 else "term_end"
            if resigned is not None and start <= resigned < ended:
                ended, via = resigned, "resigned"
            if died is not None and start < died < ended:
                ended, via = died, "died"
            tenure = cs.key(f"{SOURCE}.tenure", f"{term}:{person}:{key}")
            exact = term > 1 and not row["by_election"]
            contest = contests.get((term, key)) if exact else None
            cs.add(
                "tenure",
                id=tenure,
                person_id=person,
                body_id=assembly,
                contest_id=contest,
                from_date=start,
                from_precision="day" if exact else "unknown",
                to_date=ended,
                to_precision="day" if via in ("resigned", "died") else "unknown" if term == 1 else "day",
                acquired_via="elected",
                ended_via=via,
            )
            cs.add("citation", entity_id=tenure, source_id=album, locator={"term": term, "page": row["page"]})
            cs.add(
                "note",
                entity_id=tenure,
                kind="context",
                body_md=f"Member for {labels.get(key, capitalize(row['province']))}"
                + (", entering at a by-election; the Albüm gives no date." if row["by_election"] else "."),
            )
            if via in ("resigned", "died"):
                cs.add("tenure_event", tenure_id=tenure, kind=via, date=ended, source_id=album)
            made[term, via] += 1
            group = member_group.get((term, row["page"], row["name"]))
            if group is not None:
                _, _, _, group_start, group_end = groups[group[0]]
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=group_parties[group[0]],
                    valid_from=group_start,
                    valid_from_precision="day",
                    valid_to=group_end,
                )
            parties_here = [token.strip() for token in re.split(r"\s*[/–-]\s*", row["party"]) if token.strip()]
            if len(parties_here) == 1 and term >= 7 and parties_here[0] in ("CHP", "DP"):
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=chp if parties_here[0] == "CHP" else dp,
                    valid_from=start,
                    valid_from_precision="day" if exact else "unknown",
                    valid_to=ended,
                )
        for key, count in sorted(made.items()):
            print(key, count)
        return cs.write()


LISTS = {99: (2, "TCF"), 155: (3, "SCF"), 207: (4, "IND"), 261: (5, "IND"), 327: (6, "IND")}


def lists() -> pl.DataFrame:
    pdf = raw.cached(SOURCE, ALBUM).path
    rows = []
    for page, (term, kind) in LISTS.items():
        text = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.replace("\xad", "")
        for line in text.splitlines():
            parts = re.split(r"\s{2,}", re.sub(r"^\s*\d+\.?\s+", "", line).strip())
            if len(parts) < 2:
                continue
            name = parts[0]
            place = re.match(r"^([A-ZÇĞİÖŞÜÂÎ][A-ZÇĞİÖŞÜÂÎ -]+?)(?:\s*\((.*)\))?$", parts[1])
            if (
                place is None
                or not re.search(r"[a-zçğıöşü]", name)
                or "ADI SOYADI" in name
                or fold(place.group(1)) not in HEADINGS
            ):
                continue
            rows.append(
                {
                    "term": term,
                    "kind": kind,
                    "page": page,
                    "name": name,
                    "province": place.group(1),
                    "note": place.group(2),
                }
            )
    return pl.DataFrame(rows)
