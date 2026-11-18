"""Election Commission of Nepal: constituency results of the House of Representatives and Provincial Assembly
elections at the finest unit the commission publishes (the constituency), with the 2082 national party-list totals."""

import difflib
import functools
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid5

import pymupdf

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.np import books
from ea.store import DATA

SOURCE = "np.eci"
NEPAL = str(uuid5(package.NAMESPACE, "polity\x1fNP"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNPL"))
PORTAL = "https://result.election.gov.np/"
HANDLER = PORTAL + "Handlers/SecureJson.ashx?file=JSONFiles/"
WAYBACK = "https://web.archive.org/web/"
LOCAL = DATA / "inbox" / "manual" / SOURCE
INDEPENDENT = "स्वतन्त्र"
GENDER = {"पुरुष": "male", "महिला": "female"}

DISTRICTS_URL = HANDLER + "Election2082/Local/Lookup/districts.json"
PROVINCES_URL = HANDLER + "Election2082/Local/Lookup/states.json"
HOR_2079 = PORTAL + "ElectionResultCentral2079.aspx"
PA_2079 = PORTAL + "ElectionResultState2079.aspx"
HOR_2079_BI = PORTAL + "ElectionResultCentral2079bi.aspx"
HOR_2082 = HANDLER + "ElectionResultCentral2082.txt"
PR_2082 = (
    WAYBACK
    + "20260812193515id_/https://result.election.gov.np/Handlers/SecureJson.ashx"
    + "?file=JSONFiles/Election2082/Common/PRHoRPartyTop5.txt"
)
WINS_2082 = (
    WAYBACK
    + "20260312171351id_/https://result.election.gov.np/Handlers/SecureJson.ashx"
    + "?file=JSONFiles/Election2082/Common/HoRPartyTop5.txt"
)
HOR_2074 = WAYBACK + "20260504093822id_/https://election.gov.np/ecn/uploads/userfiles/ElectionResultBook/HoR2074.pdf"
PA_2074 = WAYBACK + "20260819225743id_/https://election.gov.np/ecn/uploads/userfiles/ElectionResultBook/PA2074.pdf"
BOOKS_URL = "https://election.gov.np/"
FILES = [DISTRICTS_URL, PROVINCES_URL, HOR_2079, PA_2079, HOR_2079_BI, HOR_2082, PR_2082, WINS_2082]
BOOK_FILES = [HOR_2074, PA_2074]

ELECTIONS: dict[str, dict[str, Any]] = {
    "2017-11-26": {"kind": "general", "year": 2017, "bs": "२०७४ मंसिर १०", "hor": None, "pa": None, "phase": 1},
    "2017-12-07": {"kind": "general", "year": 2017, "bs": "२०७४ मंसिर २१", "hor": None, "pa": None, "phase": 2},
    "2022-11-20": {"kind": "general", "year": 2022, "bs": "२०७९ मंसिर ४", "hor": HOR_2079, "pa": PA_2079},
    "2023-04-23": {"kind": "by_election", "year": 2023, "bs": "२०८० वैशाख १०", "hor": HOR_2079_BI, "pa": None},
    "2026-03-05": {"kind": "general", "year": 2026, "bs": "२०८२ फागुन २१", "hor": HOR_2082, "pa": None},
}

PROVINCES_EN = {
    1: "Koshi",
    2: "Madhesh",
    3: "Bagmati",
    4: "Gandaki",
    5: "Lumbini",
    6: "Karnali",
    7: "Sudurpashchim",
}
DISTRICTS_EN = {
    1: "Taplejung", 2: "Panchthar", 3: "Ilam", 4: "Jhapa", 5: "Sankhuwasabha", 6: "Terhathum",
    7: "Bhojpur", 8: "Dhankuta", 9: "Morang", 10: "Sunsari", 11: "Solukhumbu", 12: "Khotang",
    13: "Okhaldhunga", 14: "Udayapur", 15: "Saptari", 16: "Siraha", 17: "Dolakha", 18: "Ramechhap",
    19: "Sindhuli", 20: "Dhanusha", 21: "Mahottari", 22: "Sarlahi", 23: "Rasuwa", 24: "Dhading",
    25: "Nuwakot", 26: "Kathmandu", 27: "Bhaktapur", 28: "Lalitpur", 29: "Kavrepalanchok",
    30: "Sindhupalchok", 31: "Makwanpur", 32: "Rautahat", 33: "Bara", 34: "Parsa", 35: "Chitwan",
    36: "Gorkha", 37: "Manang", 38: "Lamjung", 39: "Kaski", 40: "Tanahun", 41: "Syangja",
    42: "Gulmi", 43: "Palpa", 44: "Arghakhanchi", 45: "Nawalpur", 46: "Rupandehi", 47: "Kapilvastu",
    48: "Mustang", 49: "Myagdi", 50: "Baglung", 51: "Parbat", 52: "Eastern Rukum", 53: "Rolpa",
    54: "Pyuthan", 55: "Salyan", 56: "Dang", 57: "Dolpa", 58: "Mugu", 59: "Jumla", 60: "Kalikot",
    61: "Humla", 62: "Jajarkot", 63: "Dailekh", 64: "Surkhet", 65: "Banke", 66: "Bardiya",
    67: "Bajura", 68: "Achham", 69: "Bajhang", 70: "Doti", 71: "Kailali", 72: "Darchula",
    73: "Baitadi", 74: "Dadeldhura", 75: "Kanchanpur", 77: "Parasi", 78: "Western Rukum",
}  # fmt: skip


REGISTERED, CAST, VALID, INVALID = 2, 3, 4, 5


def tag_of(rows_: list[dict[str, Any]]) -> str:
    return str(rows_[0]["tag"])


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


NCOMMISSION = key("source", SOURCE)
BOOKS = key("source", "result_books_2074")
HOR = key("body", "house_of_representatives")
FPTP = key("electoral_system", "fptp")
PR = key("electoral_system", "house_pr")


def fetch() -> None:
    """Read the files the commission's portal serves (some only to a browser) that were saved by hand."""
    fetch_books()
    index = json.loads((LOCAL / "index.json").read_text())
    for url, entry in index.items():
        if entry.get("source", url) not in FILES:
            continue
        raw.store(
            SOURCE,
            entry.get("source", url),
            (LOCAL / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def fetch_books() -> None:
    """Read the two 2074 result books from the snapshots of the commission's site."""
    for url in BOOK_FILES:
        raw.fetch(SOURCE, url, suffix=".pdf")


def load(url: str) -> list[dict[str, Any]]:
    return json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))


@functools.cache
def lookups() -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    districts = {int(d["id"]): d for d in load(DISTRICTS_URL) if d["id"] in DISTRICTS_EN}
    provinces = {int(p["id"]): str(p["name"]) for p in load(PROVINCES_URL)}
    return districts, provinces


GENDER_NE = {value: key_ for key_, value in books.GENDERS.items()}
HOR_PAGES = (73, 148)
PA_PAGES = (29, 376)
PA_LISTS = (74, 159, 233, 270, 330, 359, 390)  # the first page of each province's party-list table, in order
PARTY_TYPOS = {
    "नेपाल कम्युनिष्ट पार्टी एमाले": "नेपाल कम्युनिष्ट पार्टी (एमाले)",
    "एकीकृत राष्ट्रिय प्रजातन्त्र पार्टी राष्ट्रवादी": "एकीकृत राष्ट्रिय प्रजातन्त्र पार्टी (राष्ट्रवादी)",
    "नेपाल कम्युनिष्ट पार्टी (माक्सवादी)": "नेपाल कम्युनिष्ट पार्टी (मार्क्सवादी)",
    "राष्ट्रिय प्रजातन्त्र पार्टी (प्र.)": "राष्ट्रिय प्रजातन्त्र पार्टी (प्रजातान्त्रिक)",
    "देशभक्त जनगणतान्त्रिक मोर्चा": "देशभक्त जनगणतान्त्रिक मोर्चा नेपाल",
    "नेपाल लोकतान्त्रिक जनता काँगे्रस": "नेपाल लोकतान्त्रिक जनता काँग्रेस",
    "नेपाली काँग्रेस (वी.पी.)": "नेपाली काँग्रेस (बी.पी.)",
}


def _spaced(name: str) -> str:
    """A party name with single spaces, a space before a bracket, none inside it, and no stray comma."""
    text = " ".join(name.replace("\u200d", "").split())
    text = re.sub(r"\s*\(\s*", " (", text)
    text = re.sub(r"\s*\)", ")", text)
    return re.sub(r"\s*,\s*", " ", text).strip()


def _party_key(name: str) -> str:
    return re.sub(r"\s+", "", _norm(_spaced(name)))


@functools.cache
def _district_names() -> dict[int, str]:
    return {i: _squash(str(d["name"])) for i, d in lookups()[0].items()}


def _squash(text: str) -> str:
    for a, b in (
        ("ी", "ि"), ("ू", "ु"), ("श", "स"), ("ष", "स"), ("ण", "न"), ("व", "ब"), ("ङ्ग", "ंग"), ("ञ्च", "ंच"),
        ("न्त", "ंत"), ("म्ब", "ंब"), ("ञ्", "ं"), ("ङ्", "ं"), ("ं", ""), ("ः", ""),
    ):  # fmt: skip
        text = text.replace(a, b)
    return _norm(text)


def book_district(name: str) -> int:
    """The district a book's (variably spelled) district name stands for."""
    text = " ".join(re.sub(r"\bनं\b\.?|\d+|निर्वाचन|क्षेत्र", " ", name).split())
    if "नवलपरासी" in text:
        return 45 if "पूर्व" in text else 77
    if "रुकुम" in text:
        return 52 if "पूर्व" in text else 78
    squashed = _squash(text)
    scores = sorted(
        ((difflib.SequenceMatcher(None, squashed, c).ratio(), i) for i, c in _district_names().items()), reverse=True
    )
    if scores[0][0] < 0.7 or scores[0][0] - scores[1][0] < 0.1:
        raise ValueError(f"district {name!r} is not clearly one of the districts: {scores[:2]}")
    return scores[0][1]


@functools.cache
def _book(url: str) -> pymupdf.Document:
    return pymupdf.open(raw.cached(SOURCE, url).path)


@functools.cache
def book() -> dict[str, Any]:
    """Both 2074 result books read into the rows the contests are built from."""
    hor_path, pa_path = raw.cached(SOURCE, HOR_2074).path, raw.cached(SOURCE, PA_2074).path
    hor_doc, pa_doc = _book(HOR_2074), _book(PA_2074)
    phases = {(book_district(name), no): phase for (name, no), phase in books.phases(hor_doc).items()}
    declared = {(book_district(w.district), w.number): w for w in books.winners(hor_doc)}
    hor_blocks = books.blocks(hor_path, *HOR_PAGES)
    pa_blocks = books.pa_blocks(pa_path, *PA_PAGES)
    hor_list = books.national_list(hor_doc)
    pa_lists = [books.party_list(pa_doc, start) for start in PA_LISTS]

    # one spelling for each party: the one already in the archive, else the most common in the books
    known = {_party_key(n): n for n in party_names(with_books=False)}
    spelled: dict[str, Counter[str]] = defaultdict(Counter)

    def fix(name: str) -> str:
        return _spaced(PARTY_TYPOS.get(_spaced(name), name))

    for block in hor_blocks:
        for r in block.rows:
            spelled[_party_key(fix(r.party))][fix(r.party)] += 1
    for pa_block in pa_blocks:
        for r in pa_block.rows:
            spelled[_party_key(fix(r.party))][fix(r.party)] += 1
    for listing in (hor_list.rows, *pa_lists):
        for r in listing:
            lead = fix(r.name.split("/")[0])
            spelled[_party_key(lead)][lead] += 1
    canon = {k: known.get(k) or c.most_common(1)[0][0] for k, c in spelled.items()}

    def party_name(name: str) -> str:
        return INDEPENDENT if name == INDEPENDENT else canon[_party_key(fix(name))]

    def lead_name(label: str) -> str:
        return canon[_party_key(fix(label.split("/")[0]))]

    hor: list[dict[str, Any]] = []
    totals: dict[str, tuple[int, int, int, int]] = {}
    fallbacks: list[str] = []
    for block in hor_blocks:
        d = book_district(block.district)
        tag = f"hor:{d}-{block.number}"
        if not any(r.elected for r in block.rows):
            top = max(block.rows, key=lambda r: r.votes or 0)
            listed = declared.get((d, block.number))
            if listed is None or listed.votes != top.votes:
                raise ValueError(f"no declared winner for {block.district} {block.number}")
            top.elected = True
            fallbacks.append(tag)
        totals[tag] = (block.registered, block.cast, block.valid, block.invalid)
        for i, r in enumerate(block.rows, 1):
            hor.append(_row(r, i, d, block.number, None, phases[(d, block.number)], tag, party_name))
    pa: list[dict[str, Any]] = []
    for pa_block in pa_blocks:
        d = book_district(pa_block.district)
        tag = f"pa:{d}-{pa_block.hor}-{pa_block.number}"
        totals[tag] = (pa_block.registered, pa_block.cast, pa_block.valid, pa_block.invalid)
        for i, r in enumerate(pa_block.rows, 1):
            pa.append(_row(r, i, d, pa_block.hor, pa_block.number, phases[(d, pa_block.hor)], tag, party_name))
    return {
        "hor": hor,
        "pa": pa,
        "totals": totals,
        "fallbacks": fallbacks,
        "hor_list": [(_spaced(r.name), lead_name(r.name), r) for r in hor_list.rows],
        "hor_total": hor_list.total,
        "hor_seats": hor_list.seats,
        "pa_lists": [[(_spaced(r.name), lead_name(r.name), r) for r in rows] for rows in pa_lists],
        "declared": declared,
        "parties": sorted(set(canon.values()) - {INDEPENDENT}),
    }


def _row(
    r: books.Row, i: int, d: int, hor: int, pa: int | None, phase: int, tag: str, party_name: Any
) -> dict[str, Any]:
    return {
        "CandidateID": str(i),
        "CandidateName": r.name,
        "Gender": GENDER_NE[r.gender],
        "PoliticalPartyName": party_name(r.party),
        "TotalVoteReceived": r.votes,
        "Remarks": "Elected" if r.elected else "",
        "Age": r.age,
        "district": d,
        "hor": hor,
        "pa": pa,
        "phase": phase,
        "tag": tag,
        "page": r.page,
    }


def _norm(text: str) -> str:
    return text.replace("ङ्ग", "ंग").replace("ङ", "ं").replace("ुं", "ु").replace("ँ", "ं").replace(" ", "")


def rows(url: str, districts: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidate rows with the district id, the House constituency number, and (provincial) the seat number."""
    by_name = {_norm(str(d["name"])): i for i, d in districts.items()}
    out = []
    for r in load(url):
        row = dict(r)
        if "DistrictCd" in row:
            district = int(row["DistrictCd"])
        else:
            district = by_name[_norm(str(row["DistrictName"]))]
        row["district"] = district
        if row.get("CenterConstID") is None:
            row["hor"], row["pa"] = int(row["SCConstID"]), None
        else:
            row["hor"], row["pa"] = int(row["CenterConstID"]), int(row["SCConstID"])
        out.append(row)
    return out


def grids(day: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    districts = lookups()[0]
    cfg = ELECTIONS[day]
    if cfg.get("phase"):
        data = book()
        return (
            [r for r in data["hor"] if r["phase"] == cfg["phase"]],
            [r for r in data["pa"] if r["phase"] == cfg["phase"]],
        )
    hor = rows(str(cfg["hor"]), districts)
    pa = rows(str(cfg["pa"]), districts) if cfg["pa"] else []
    return hor, pa


def seats(day: str) -> tuple[set[tuple[int, int]], set[tuple[int, int, int]]]:
    hor, pa = grids(day)
    return {(r["district"], r["hor"]) for r in hor}, {(r["district"], r["hor"], r["pa"]) for r in pa}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in ELECTIONS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def province_of(district: int) -> int:
    return int(lookups()[0][district]["parentId"])


def hor_code(district: int, number: int) -> str:
    return f"{district}-{number}"


def pa_code(district: int, number: int, seat: int) -> str:
    return f"{district}-{number}-{seat}"


def source(p: Package) -> None:
    p.add(
        "source",
        id=NCOMMISSION,
        kind="official_body",
        publisher="निर्वाचन आयोग, नेपाल (Election Commission, Nepal)",
        title="Election results portal of the Election Commission, Nepal",
        url=PORTAL,
        license="No license stated; published by the electoral authority for public use",
        attribution="Election Commission, Nepal",
    )
    p.add(
        "source",
        id=BOOKS,
        kind="official_body",
        publisher="निर्वाचन आयोग, नेपाल (Election Commission, Nepal)",
        title="Result books of the 2074 House of Representatives and Provincial Assembly elections",
        url=BOOKS_URL,
        license="No license stated; published by the electoral authority for public use",
        attribution="Election Commission, Nepal",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Nepal's 7 provinces, 77 districts, House of Representatives constituencies, Provincial Assembly "
        "constituencies, and the national count, as the Election Commission prints them",
    )
    p.external |= {NEPAL, COUNTRY}
    districts, provinces = lookups()
    p.uses(raw.cached(SOURCE, DISTRICTS_URL))
    p.uses(raw.cached(SOURCE, PROVINCES_URL))
    for code, rank, kind, noun in (
        ("np.nation", 0, "administrative", "country"),
        ("np.province", 1, "administrative", "province"),
        ("np.district", 2, "administrative", "district"),
        ("np.hor", 3, "electoral_district", "House of Representatives constituency"),
        ("np.pa", 4, "electoral_district", "Provincial Assembly constituency"),
    ):
        p.add("scheme", code=code, polity_id=NEPAL, kind=kind, rank=rank, noun=noun, code_system=None)
    for id, name in provinces.items():
        a = area("np.province", str(id))
        p.add("area", id=a, scheme_code="np.province")
        p.add("area_code", area_id=a, code=str(id))
        p.add("area_parent", area_id=a, parent_id=COUNTRY)
        p.name(a, name, "ne", "official")
        p.name(a, f"{PROVINCES_EN[id]} Province", "en", "official", False)
    for id, d in districts.items():
        a = area("np.district", str(id))
        p.add("area", id=a, scheme_code="np.district")
        p.add("area_code", area_id=a, code=str(id))
        p.add("area_parent", area_id=a, parent_id=area("np.province", str(d["parentId"])))
        p.name(a, str(d["name"]), "ne", "official")
        p.name(a, DISTRICTS_EN[id], "en", "official", False)
    hor_seats: set[tuple[int, int]] = set()
    pa_seats: set[tuple[int, int, int]] = set()
    for day in ELECTIONS:
        h, q = seats(day)
        hor_seats |= h
        pa_seats |= q
    for d, n in sorted(hor_seats):
        a = area("np.hor", hor_code(d, n))
        p.add("area", id=a, scheme_code="np.hor")
        p.add("area_code", area_id=a, code=hor_code(d, n))
        p.add("area_parent", area_id=a, parent_id=area("np.district", str(d)))
        p.name(a, f"{districts[d]['name']} - {n}", "ne", "official")
        p.name(a, f"{DISTRICTS_EN[d]} {n}", "en", "official", False)
    for d, n, s in sorted(pa_seats):
        a = area("np.pa", pa_code(d, n, s))
        p.add("area", id=a, scheme_code="np.pa")
        p.add("area_code", area_id=a, code=pa_code(d, n, s))
        p.add("area_parent", area_id=a, parent_id=area("np.hor", hor_code(d, n)))
        p.name(a, f"{districts[d]['name']} - {n}({s})", "ne", "official")
        p.name(a, f"{DISTRICTS_EN[d]} {n}({s})", "en", "official", False)
    return p


def party(name: str) -> str:
    return key("party", name)


def party_names(with_books: bool = True) -> set[str]:
    """Every party the elections name; without `with_books`, only those the portal's files name."""
    names: set[str] = set()
    for day, cfg in ELECTIONS.items():
        if not cfg.get("phase"):
            for grid in grids(day):
                names |= {str(r["PoliticalPartyName"]) for r in grid}
    names |= {str(r["PoliticalPartyName"]) for r in load(PR_2082)}
    if with_books:
        names |= set(book()["parties"])
    return names - {INDEPENDENT}


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Nepal's parties, House of Representatives, Provincial Assemblies, and electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEPAL}
    source(p)
    for url in FILES:
        p.artifact(raw.cached(SOURCE, url), NCOMMISSION)
    for url in BOOK_FILES:
        p.artifact(raw.cached(SOURCE, url), BOOKS)
    p.add("body", id=HOR, polity_id=NEPAL, kind="lower_house")
    p.name(HOR, "House of Representatives", "en", "official")
    p.name(HOR, "प्रतिनिधि सभा", "ne", "native", False)
    for id, name in PROVINCES_EN.items():
        body = key("body", "provincial_assembly", id)
        p.add("body", id=body, polity_id=NEPAL, kind="unicameral")
        p.name(body, f"{name} Provincial Assembly", "en", "official")
        p.name(body, f"{lookups()[1][id].removesuffix(' प्रदेश')} प्रदेश सभा", "ne", "native", False)
    for id, rules, method in (
        (
            FPTP,
            {"formula": "fptp"},
            "One member for each constituency, elected by the most votes.",
        ),
        (
            PR,
            {
                "formula": "modified_sainte_lague",
                "firstDivisor": 1.4,
                "threshold": {"percent": 3, "level": "party", "scope": "national"},
            },
            (
                "House of Representatives list seats are allocated nationally among parties by the modified "
                "Sainte-Laguë method with a first divisor of 1.4, to parties with at least 3 percent of the valid "
                "party-list votes."
            ),
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=NEPAL,
            valid_from=datetime(2015, 9, 20).date(),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for name in sorted(party_names()):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=NEPAL)
        p.name(id, " ".join(name.split()), "ne", "official")
    return p


METHOD = (
    "Counted by the Election Commission, Nepal, and published on its results portal for each constituency: the "
    "votes each candidate received and the candidate it marks as elected. The portal gives no registered, cast, "
    "valid, or invalid totals for a constituency, so none are stored."
)


BOOK_METHOD = (
    "Counted by the Election Commission, Nepal, and printed for each constituency in its result book: the voters "
    "registered, the votes cast, and the valid and invalid votes, then each candidate's votes and the candidate it "
    "marks as elected. Where the book prints a candidate's votes in the wrong cell or leaves one out, the figure is "
    "stored as printed or left out, and the contest's valid total is the one printed."
)


def election(day: str) -> Package:
    cfg = ELECTIONS[day]
    kind = str(cfg["kind"])
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day} Nepal {'general' if kind == 'general' else 'by-election'} by constituency from the Election "
        "Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEPAL, COUNTRY}
    districts = lookups()[0]
    hor, pa = grids(day)
    phase = cfg.get("phase")
    if phase:
        for url in BOOK_FILES:
            p.uses(raw.cached(SOURCE, url))
    for url in (cfg["hor"], cfg["pa"]):
        if url:
            p.uses(raw.cached(SOURCE, str(url)))
    year = cfg["year"]
    slug_kind = "general" if kind == "general" else "by-election"
    path = f"np/{day}-{slug_kind}"
    result_source = BOOKS if phase else NCOMMISSION
    el = key("election", day)
    p.add(
        "election",
        id=el,
        polity_id=NEPAL,
        kind=kind,
        date_from=datetime.fromisoformat(day).date(),
        date_to=datetime.fromisoformat(day).date(),
        original_text=cfg["bs"],
        status="held",
    )
    title = f"{year} Nepal {'general election' if kind == 'general' else 'by-election'}"
    if phase:
        title += f", {'first' if phase == 1 else 'second'} phase"
    p.name(el, title, "en", "official")
    p.add("slug", path=path, entity_id=el, is_current=True)
    people: set[str] = set()
    short: list[str] = []

    def contest(
        level: str, tag: str, ne: str, en: str, slug: str, body: str, scheme: str, code: str, rows_: list
    ) -> None:
        c = key("contest", day, tag)
        p.add(
            "contest",
            id=c,
            election_id=el,
            kind="body_seats",
            body_id=body,
            district_area_id=area(scheme, code),
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
        )
        p.name(c, f"{year} {level} election, {en}", "en", "official")
        p.name(c, ne, "ne", "native", False)
        p.add("slug", path=f"{path}/{slug}", entity_id=c, is_current=True)
        rs = key("result_set", day, tag)
        p.add(
            "result_set",
            id=rs,
            election_id=el,
            contest_id=c,
            stage="official",
            kind="counted",
            source_id=result_source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", rs),
            entity_id=rs,
            kind="methodology",
            body_md=BOOK_METHOD if phase else METHOD,
            lang="en",
        )
        if phase:
            pages = sorted({int(r["page"]) for r in rows_})
            p.add(
                "citation",
                id=key("citation", rs),
                entity_id=rs,
                source_id=BOOKS,
                locator={
                    "file": "HoR2074.pdf" if level == "House of Representatives" else "PA2074.pdf",
                    "pages": pages,
                },
            )
        unit = key("unit", day, tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEPAL,
            scheme_code=scheme,
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=area(scheme, code),
            kind="standard",
        )
        winners = 0
        if phase:
            for measure, value in zip((REGISTERED, CAST, VALID, INVALID), book()["totals"][tag_of(rows_)], strict=True):
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        for r in rows_:
            cid = str(r["CandidateID"])
            option = key("option", day, tag, cid)
            person = key("person", day, tag, cid)
            if person in people:
                raise ValueError(f"duplicate candidate {tag} {cid}")
            people.add(person)
            name = " ".join(str(r["CandidateName"]).split())
            solo = r["PoliticalPartyName"] == INDEPENDENT
            member = None if solo else party(str(r["PoliticalPartyName"]))
            p.add("person", id=person, gender=GENDER.get(str(r["Gender"])))
            p.name(person, name, "ne", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=c,
                kind="candidate",
                party_id=member,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", day, tag, cid),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=member,
                is_independent=solo,
            )
            if r["TotalVoteReceived"] is not None:
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=int(r["TotalVoteReceived"]),
                )
            if r["Remarks"] == "Elected":
                winners += 1
                p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
        if winners != 1:
            raise ValueError(f"{tag} has {winners} elected candidates")
        if phase:
            printed = [r["TotalVoteReceived"] for r in rows_]
            valid = book()["totals"][tag_of(rows_)][2]
            if None in printed or sum(v for v in printed if v is not None) != valid:
                short.append(
                    f"{en}: the book prints {valid} valid votes and "
                    f"{sum(v for v in printed if v is not None)} for the candidates"
                    f"{' (one candidate has no votes printed)' if None in printed else ''}"
                )

    groups: dict[tuple[int, int], list] = defaultdict(list)
    for r in hor:
        groups[(r["district"], r["hor"])].append(r)
    for (d, n), group in sorted(groups.items()):
        en = DISTRICTS_EN[d]
        contest(
            "House of Representatives",
            f"hor:{d}-{n}",
            f"प्रतिनिधि सभा निर्वाचन, {districts[d]['name']} - {n}",
            f"{en} {n}",
            f"house/{changeset.slug(en)}-{n}",
            HOR,
            "np.hor",
            hor_code(d, n),
            group,
        )
    pgroups: dict[tuple[int, int, int], list] = defaultdict(list)
    for r in pa:
        pgroups[(r["district"], r["hor"], r["pa"])].append(r)
    for (d, n, s), group in sorted(pgroups.items()):
        en = DISTRICTS_EN[d]
        contest(
            "Provincial Assembly",
            f"pa:{d}-{n}-{s}",
            f"प्रदेश सभा निर्वाचन, {districts[d]['name']} - {n}({s})",
            f"{en} {n}({s})",
            f"province/{changeset.slug(en)}-{n}-{s}",
            key("body", "provincial_assembly", province_of(d)),
            "np.pa",
            pa_code(d, n, s),
            group,
        )
    if day == "2026-03-05":
        _list_contest(p, el, path, int(str(year)))
    if day == "2017-12-07":
        short += _book_lists(p, el, path, int(str(year)), day)
    if short:
        p.accept("accounting.votes_valid", "The result book's own figures disagree: " + "; ".join(short) + ".")
    return p


def _book_lists(p: Package, el: str, path: str, year: int, day: str) -> list[str]:
    """The party-list votes of both books: the national House list, and one list for each province's assembly."""
    data = book()
    pa_doc = _book(PA_2074)
    short: list[str] = []

    def list_contest(
        tag: str,
        scheme: str,
        code: str,
        area_id: str,
        body: str,
        en: str,
        ne: str,
        slug: str,
        rows_: list,
        total: int,
        source: str,
    ) -> None:
        c = key("contest", day, tag)
        p.add(
            "contest",
            id=c,
            election_id=el,
            kind="list_allocation",
            body_id=body,
            district_area_id=area_id,
            round_number=1,
            electoral_system_id=PR,
            default_result_kind="counted",
        )
        p.name(c, en, "en", "official")
        p.name(c, ne, "ne", "native", False)
        p.add("slug", path=f"{path}/{slug}", entity_id=c, is_current=True)
        rs = key("result_set", day, tag)
        p.add(
            "result_set",
            id=rs,
            election_id=el,
            contest_id=c,
            stage="official",
            kind="counted",
            source_id=BOOKS,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=LIST_METHOD, lang="en")
        pages = sorted({r.page for _, _, r in rows_})
        p.add(
            "citation",
            id=key("citation", rs),
            entity_id=rs,
            source_id=BOOKS,
            locator={"file": source, "pages": pages},
        )
        unit = key("unit", day, tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEPAL,
            scheme_code=scheme,
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=area_id,
            kind="standard",
        )
        for label, lead, r in rows_:
            option = key("option", day, tag, r.serial)
            p.add(
                "contest_option",
                id=option,
                contest_id=c,
                kind="list",
                party_id=party(lead),
                ballot_position=r.serial,
                ballot_label=label,
            )
            p.add(
                "result_value",
                result_set_id=rs,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=r.numbers[0],
            )
            seats = data["hor_seats"].get(r.numbers[0]) if tag == "pr" else None
            if seats:
                p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=seats)
        p.add(
            "result_value",
            result_set_id=rs,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=VALID,
            count_round=0,
            flags=0,
            value=total,
        )
        p.add("declared", result_set_id=rs, option_id=None, measure=VALID, value=total)
        got = sum(r.numbers[0] for _, _, r in rows_)
        if got != total:
            short.append(f"{en}: the book prints {total} valid votes and {got} for the parties")

    p.uses(raw.cached(SOURCE, HOR_2074))
    list_contest(
        "pr",
        "np.nation",
        "NPL",
        COUNTRY,
        HOR,
        f"{year} House of Representatives election, party-list vote",
        "प्रतिनिधि सभा निर्वाचन, समानुपातिक",
        "house-party-list",
        data["hor_list"],
        data["hor_total"],
        "HoR2074.pdf",
    )
    for province, rows_ in enumerate(data["pa_lists"], 1):
        name = PROVINCES_EN[province]
        list_contest(
            f"pr:{province}",
            "np.province",
            str(province),
            area("np.province", str(province)),
            key("body", "provincial_assembly", province),
            f"{year} Provincial Assembly election, {name} party-list vote",
            f"प्रदेश सभा निर्वाचन, {lookups()[1][province].removesuffix(' प्रदेश')} समानुपातिक",
            f"province/{changeset.slug(name)}-party-list",
            rows_,
            books.list_total(pa_doc, PA_LISTS[province - 1]),
            "PA2074.pdf",
        )
    return short


LIST_METHOD = (
    "The party-list votes of each party as printed in the Election Commission's result book: nationwide for the "
    "House of Representatives, and within each province for its Provincial Assembly. A list that several parties "
    "contested under one symbol is one option, labeled as the book prints it and tied to the party it names first. "
    "The list was voted on both polling days; it is held with the second phase. The seats of the House list are the "
    "ones the book prints for the parties above the 3 percent threshold. The book's allocation of list seats in "
    "provinces, and the votes by constituency, are not stored here yet."
)


def _list_contest(p: Package, el: str, path: str, year: int) -> None:
    p.uses(raw.cached(SOURCE, PR_2082))
    p.uses(raw.cached(SOURCE, WINS_2082))
    c = key("contest", "2026-03-05", "pr")
    p.add(
        "contest",
        id=c,
        election_id=el,
        kind="list_allocation",
        body_id=HOR,
        district_area_id=COUNTRY,
        round_number=1,
        electoral_system_id=PR,
        default_result_kind="counted",
    )
    p.name(c, f"{year} House of Representatives election, party-list vote", "en", "official")
    p.name(c, "प्रतिनिधि सभा निर्वाचन, समानुपातिक", "ne", "native", False)
    p.add("slug", path=f"{path}/house-party-list", entity_id=c, is_current=True)
    rs = key("result_set", "2026-03-05", "pr")
    p.add(
        "result_set",
        id=rs,
        election_id=el,
        contest_id=c,
        stage="official",
        kind="counted",
        source_id=NCOMMISSION,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", rs),
        entity_id=rs,
        kind="methodology",
        body_md=(
            "The national party-list vote total of each party, as the Election Commission's results portal "
            "published it. The portal's constituency and district breakdown of the party-list vote and the "
            "allocation of list seats are not stored here yet."
        ),
        lang="en",
    )
    unit = key("unit", "2026-03-05", "pr")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=NEPAL,
        scheme_code="np.nation",
        code="2026-03-05:NPL",
        area_relation="equals",
        area_id=COUNTRY,
        kind="standard",
    )
    for i, r in enumerate(load(PR_2082), 1):
        option = key("option", "2026-03-05", "pr", r["PoliticalPartyName"])
        p.add(
            "contest_option",
            id=option,
            contest_id=c,
            kind="list",
            party_id=party(str(r["PoliticalPartyName"])),
            ballot_position=i,
            ballot_label=" ".join(str(r["PoliticalPartyName"]).split()),
        )
        p.add(
            "result_value",
            result_set_id=rs,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=int(r["TotalVoteReceived"]),
        )


def verify() -> dict[str, Any]:
    """Compare the parsed 2082 winners with the party seat totals the portal publishes."""
    hor, _ = grids("2026-03-05")
    won = Counter(str(r["PoliticalPartyName"]) for r in hor if r["Remarks"] == "Elected")
    published = {str(r["PoliticalPartyName"]): int(r["TotWin"]) for r in load(WINS_2082)}
    return {"parsed": dict(won), "published": published, "match": dict(won) == published}


def verify_books() -> dict[str, Any]:
    """Compare what was read from the 2074 books with the totals and winners the books themselves print."""
    data = book()
    elected = [r for r in data["hor"] if r["Remarks"] == "Elected"]
    by_party = Counter(str(r["PoliticalPartyName"]) for r in elected)
    winners = {(r["district"], r["hor"]): r for r in elected}
    listed = data["declared"]
    wrong = [
        k
        for k, w in listed.items()
        if k not in winners
        or winners[k]["TotalVoteReceived"] != w.votes
        or (w.name and winners[k]["CandidateName"] != w.name)
    ]
    pa_by_party = Counter(str(r["PoliticalPartyName"]) for r in data["pa"] if r["Remarks"] == "Elected")
    return {
        "house_winners": sum(by_party.values()),
        "house_by_party": dict(by_party),
        "provincial_winners": sum(pa_by_party.values()),
        "listed_winners": len(listed),
        "listed_winners_that_differ": wrong,
        "winner_flag_missing": data["fallbacks"],
        "house_list_sum": sum(r.numbers[0] for _, _, r in data["hor_list"]),
        "house_list_printed": data["hor_total"],
        "house_list_seats": sum(data["hor_seats"].values()),
        "province_list_sums": [sum(r.numbers[0] for _, _, r in rows) for rows in data["pa_lists"]],
    }
