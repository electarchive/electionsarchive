"""Azad Jammu and Kashmir's local bodies elections of 2022, from the Election Commission's result sites: the
Commission's results system for Poonch and Mirpur divisions and its own site for Muzaffarabad division. Each
ward of a union council, municipal corporation, municipal committee, or town committee, and each union council's
seat on its district council, has its own result table with the registered voters, ballots cast, rejected ballots,
and every candidate's votes."""

import html
import re
import unicodedata
from datetime import date
from typing import NamedTuple

from ea import raw
from ea.sources.pk import ajkec_crawl as crawl

SOURCE = crawl.SOURCE
POLLS = {"Muzaffarabad": date(2022, 11, 27), "Poonch": date(2022, 12, 3), "Mirpur": date(2022, 12, 8)}
DISTRICTS = {
    "نیلم": ("Neelum", "Muzaffarabad"),
    "مظفرآباد": ("Muzaffarabad", "Muzaffarabad"),
    "جہلم ویلی": ("Jhelum Valley", "Muzaffarabad"),
    "باغ": ("Bagh", "Poonch"),
    "حویلی": ("Haveli", "Poonch"),
    "پونچھ": ("Poonch", "Poonch"),
    "سدھنوتی": ("Sudhnoti", "Poonch"),
    "کوٹلی": ("Kotli", "Mirpur"),
    "میرپور": ("Mirpur", "Mirpur"),
    "بھمبر": ("Bhimber", "Mirpur"),
}
TEHSILS = {
    "ہٹیاں": "Hattian",
    "چکار": "Chikar",
    "لیپہ": "Leepa",
    "شاردہ": "Sharda",
    "آٹھمقام": "Athmuqam",
    "مظفرآباد": "Muzaffarabad",
    "نصیر آباد/پٹیکہ": "Nasirabad/Patika",
}
DISTRICTS = {unicodedata.normalize("NFC", k): v for k, v in DISTRICTS.items()}
TEHSILS = {unicodedata.normalize("NFC", k): v for k, v in TEHSILS.items()}
KINDS = {
    1: "district_council",
    2: "union_council",
    3: "municipal_corporation",
    4: "municipal_committee",
    5: "town_committee",
}
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5


class Candidate(NamedTuple):
    source_id: str | None
    name: str
    party: str
    symbol: str
    votes: int


class Seat(NamedTuple):
    division: str
    kind: int  # 1 district council seat of a union council, 2 union council ward, 3-5 municipal ward
    district: str  # Urdu
    tehsil: str  # Urdu
    tehsil_en: str
    council: str  # Urdu
    council_en: str
    council_order: int  # position within its tehsil
    ward: str  # Urdu label, "" for a district council seat
    ward_number: int | None
    ward_order: int
    key: str  # the raw cache key of its result table
    registered: int
    polled: int
    rejected: int
    tendered: int
    candidates: list[Candidate]


def _int(text: str) -> int:
    return int(text.replace(",", "").strip() or 0)


def fold(text: str) -> str:
    """Whitespace, joiners, and brackets removed, so spelling variants of one name compare equal."""
    return re.sub(r"[\s\u200c\u200d()]+", "", unicodedata.normalize("NFC", html.unescape(text)))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def table(text: str, ermis: bool) -> tuple[list[int], list[Candidate]]:
    """The four counts above a result table and its candidate rows."""
    counts = [_int(v) for v in re.findall(r"<h1[^>]*>\s*([\d,]+)\s*</h1>", text)[:4]]
    body = text.split("<tbody>", 1)[1].split("</tbody>", 1)[0] if "<tbody>" in text else ""
    out: list[Candidate] = []
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL):
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.DOTALL)
        if len(cells) == 1 and "colspan" in cells[0][0]:
            continue  # "results for the seat have not been received yet"
        if len(cells) != 3:
            raise ValueError(f"a candidate row with {len(cells)} cells")
        ident = re.search(r'\sd="(\d+)"', cells[0][0])
        name, party = _clean(cells[0][1]), _clean(cells[1][1])
        if ermis:
            m = re.match(r"^\(([^)]*)\)\s*(.*)$", party)
            symbol, party = (m.group(1).strip(), m.group(2).strip()) if m else ("", party)
        else:
            m = re.match(r"^(.*?)\s*\(([^()]*)\)$", party)
            party, symbol = (m.group(1).strip(), m.group(2).strip()) if m else (party, "")
        out.append(Candidate(ident.group(1) if ident else None, name, party, symbol, _int(_clean(cells[2][1]))))
    return counts, out


def _opts(text: str) -> list[tuple[int, str]]:
    return crawl.options(text)


def _bilingual(label: str) -> tuple[str, str]:
    parts = re.split(r"\s{2,}", label.strip(), maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])


def _ward_number(label: str) -> int | None:
    m = re.search(r"\((\d+)\)\s*$", label)
    return int(m.group(1)) if m else None


def seats() -> list[Seat]:
    found: list[Seat] = []
    index = raw.keys(SOURCE)

    def cached(url: str) -> str | None:
        return raw.cached(SOURCE, url).path.read_text() if url in index else None

    for division in ("Poonch", "Mirpur"):
        page = cached(crawl.division_url(division))
        if page is None:
            raise ValueError(f"{division} is not fetched")
        for district, district_label in _opts(page.split('id="district-list"')[1].split("</select>")[0]):
            tehsil_list = cached(crawl.tehsils_url(district))
            for tehsil, tehsil_label in _opts(tehsil_list or ""):
                tehsil_en, tehsil_ur = _bilingual(tehsil_label)
                for order, (council, council_label) in enumerate(_opts(cached(crawl.councils_url(tehsil)) or "")):
                    council_en, council_ur = _bilingual(council_label)
                    municipal = crawl.kind_of(council_en)
                    jobs: list[tuple[int, int | None, str, int | None, int]] = []
                    if municipal is None:
                        jobs.append((1, None, "", None, 0))
                    wards = _opts(cached(crawl.wards_url(council)) or "")
                    for ward_order, (ward, ward_label) in enumerate(wards):
                        jobs.append((municipal or 2, ward, ward_label, _ward_number(ward_label), ward_order))
                    for kind, ward, ward_label, number, ward_order in jobs:
                        url = crawl.result_url(kind, district, tehsil, council, ward)
                        text = cached(url)
                        if text is None:
                            raise ValueError(f"{url} is not fetched")
                        counts, candidates = table(text, True)
                        if not candidates:
                            continue
                        found.append(
                            Seat(
                                division,
                                kind,
                                district_label,
                                tehsil_ur,
                                tehsil_en or TEHSILS.get(unicodedata.normalize("NFC", tehsil_ur), ""),
                                council_ur,
                                council_en,
                                order,
                                ward_label,
                                number,
                                ward_order,
                                url,
                                *counts,
                                candidates,
                            )
                        )
    found += _ec_seats(index)
    return found


def _ec_seats(index: set[str]) -> list[Seat]:
    out: list[Seat] = []

    def cached(url: str) -> str:
        return raw.cached(SOURCE, url).path.read_text()

    page = cached(f"{crawl.EC_SITE}/")
    for district in crawl.ec_options(page.split('id="district-list"')[1].split("</select>")[0]):
        for tehsil in crawl.ec_options(cached(crawl.ec_url("get-tehsil-ep", district_id=district))):
            ucs = crawl.ec_options(cached(crawl.ec_url("get-uc-ep", tehsile_id=tehsil, district_id=district)))
            for order, uc in enumerate(ucs):
                municipal = next((k for p, k in crawl.EC_MUNICIPAL.items() if uc.startswith(p)), None)
                wards = crawl.ec_options(
                    cached(crawl.ec_url("get-ward-ep", uc_id=uc, tehsile_id=tehsil, district_id=district))
                )
                jobs: list[tuple[int, str, int]] = []
                if municipal is None:
                    jobs.append((1, "", 0))
                kind = municipal or 2
                jobs += [(kind, w, i) for i, w in enumerate(wards)]
                for kind_, ward, ward_order in jobs:
                    name = next(n for n, k in crawl.EC_TYPES.items() if k == kind_)
                    url = crawl.ec_url("show_results", type=name, district=district, tehsil=tehsil, uc=uc, ward=ward)
                    if url not in index:
                        raise ValueError(f"{url} is not fetched")
                    counts, candidates = table(cached(url), False)
                    if not candidates:
                        continue
                    out.append(
                        Seat(
                            "Muzaffarabad",
                            kind_,
                            district,
                            tehsil,
                            TEHSILS.get(unicodedata.normalize("NFC", tehsil), ""),
                            uc,
                            "",
                            order,
                            ward,
                            None,
                            ward_order,
                            url,
                            *counts,
                            candidates,
                        )
                    )
    return out


# ---------------------------------------------------------------------------------------------------------------
# Packages

from uuid import uuid5

import polars as pl

from ea import changeset, package
from ea.package import Package

PAKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fPK"))
AJK = str(uuid5(package.NAMESPACE, "polity\x1fPK-JK"))
COUNCIL_SCHEME = "pk.ajk_council"
WARD_SCHEME = "pk.ajk_ward"
EC_PAGE = "https://ec.ajk.gov.pk/local-bodies-election-2022/"
ERMIS_PAGE = "https://ermis.admin.gok.pk/local-bodies-2022/home"
SCHEDULE = "https://ec.ajk.gov.pk/wp-content/uploads/2022/11/Division-wise-schedule.pdf"
NOTIFICATION = "https://ec.ajk.gov.pk/wp-content/uploads/2022/10/LG-Election-Schedule-2022.pdf"
BUILT = ("Muzaffarabad", "Poonch")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "ajkec")
SYSTEM = key("electoral_system", "fptp")

# canonical party code -> (English name, Urdu name)
PARTIES = {
    "PTI": ("Pakistan Tehreek-e-Insaf", "پاکستان تحریک انصاف"),
    "PMLN": ("Pakistan Muslim League (N)", "پاکستان مسلم لیگ (ن)"),
    "PPP": ("Pakistan Peoples Party", "پاکستان پیپلز پارٹی"),
    "TLP": ("Tehreek-e-Labbaik Pakistan", "تحریک لبیک پاکستان"),
    "MC": ("All Jammu and Kashmir Muslim Conference", "مسلم کانفرنس"),
    "JI": ("Jamaat-e-Islami", "جماعت اسلامی"),
    "JKPP": ("Jammu Kashmir Peoples Party", "جموں کشمیر پیپلز پارٹی"),
    "JUI": ("Jamiat Ulema-e-Islam", "جمعیت علمائے اسلام"),
    "MQM": ("Muttahida Qaumi Movement Pakistan", "ایم کیو ایم"),
    "JKLL": ("Jammu Kashmir Liberation League", "جموں کشمیر لبریشن لیگ"),
    "JKUM": ("Jammu and Kashmir United Movement", "جموں و کشمیر یونائیٹڈ موومنٹ"),
    "RHP": ("Rai Haq Party", "رائے حق پارٹی"),
    "AT": ("Awami Tehreek", "عوامی تحریک"),
}
PARTY_FORMS = {
    "پاکستانتحریکانصاف": "PTI",
    "پاکستانمسلملیگن": "PMLN",
    "پاکستانپیپلزپارٹی": "PPP",
    "پاکستانپپیلزپارٹی": "PPP",
    "تحریکلبیکپاکستان": "TLP",
    "پاکستانتحریکلبیک": "TLP",
    "تحریکلبیک": "TLP",
    "مسلمکانفرنس": "MC",
    "آلجموںوکشمیرمسلمکانفرنس": "MC",
    "جماعتاسلامی": "JI",
    "جموںکشمیرپیپلزپارٹی": "JKPP",
    "JKPP": "JKPP",
    "جےکےپیپی": "JKPP",
    "جمعیتعلماءاسلام": "JUI",
    "جمعیتعلمائےاسلام": "JUI",
    "جمعیتعلمائےاسلامجموںوکشمیر": "JUI",
    "جمعیتعلمائےاسلامجموںکشمیر": "JUI",
    "JUIF": "JUI",
    "ایمکیوایم": "MQM",
    "جموںکشمیرلبریشنلیگ": "JKLL",
    "جموںوکشمیریونائیٹڈمومنٹ": "JKUM",
    "رائےحقپارٹی": "RHP",
    "عوامیتحریک": "AT",
}


PARTY_FORMS = {fold(k): v for k, v in PARTY_FORMS.items()}


def party_code(text: str) -> str | None:
    """The canonical code of a party as the sites spell it ("IND" for an independent, None for no party printed)."""
    folded = fold(text)
    if folded.startswith(fold("آزاد")):
        return "IND"
    if not folded:
        return None
    return PARTY_FORMS[folded]


def district_en(seat: Seat) -> str:
    return DISTRICTS[unicodedata.normalize("NFC", seat.district)][0]


def council_label(seat: Seat) -> str:
    """The council's English name, or its Urdu name where the site gives none."""
    return seat.council_en or seat.council


def council_group(seat: Seat) -> str:
    return "union" if seat.kind in (1, 2) else KINDS[seat.kind]


def council_area(seat: Seat) -> str:
    return key("area", "council", seat.district, seat.tehsil, seat.council, council_group(seat))


def council_body(seat: Seat) -> str:
    return key("body", "council", seat.district, seat.tehsil, seat.council, council_group(seat))


def district_body(seat: Seat) -> str:
    return key("body", "district_council", seat.district)


def ward_area(seat: Seat) -> str:
    return key("area", "ward", seat.district, seat.tehsil, seat.council, council_group(seat), seat.ward)


def body_name(seat: Seat) -> str:
    label = council_label(seat)
    if seat.kind in (1, 2):
        return f"Union Council {label}, {district_en(seat)}"
    return f"{label}, {district_en(seat)}"


def ward_en(seat: Seat) -> str:
    return f"Ward {seat.ward_number}" if seat.ward_number is not None else f"Ward {seat.ward_order + 1}"


def built() -> list[Seat]:
    """The seats whose result tables hold votes, in the divisions with published results."""
    return [s for s in seats() if s.division in BUILT and sum(c.votes for c in s.candidates) > 0]


def _artifacts(done: list[Seat]) -> list[str]:
    """The raw files the packages cite: every list that names a unit and every result table of a seat built."""
    keep = {s.key for s in done}
    out = []
    for url in sorted(raw.keys(SOURCE)):
        if ("getwardResults" in url or "show_results" in url) and url not in keep:
            continue
        if "/candidates?" in url and not any(f"/division/{d}/" in url for d in BUILT):
            continue
        out.append(url)
    return out


def winners_url(division: str, kind: int) -> str:
    return f"{crawl.SITE}/division/{division}/candidates?counciltype={kind}"


def winners(division: str) -> dict[tuple[int, str, str], set[str]]:
    """The names of the candidates the Commission's list of winners names, by council type, district, and council."""
    out: dict[tuple[int, str, str], set[str]] = {}
    for kind in KINDS:
        url = winners_url(division, kind)
        if url not in raw.keys(SOURCE):
            continue
        text = raw.cached(SOURCE, url).path.read_text()
        for row in re.findall(r'<tr class="resultstableheading">(.*?)</tr>', text, re.DOTALL):
            cells = [_clean(v) for v in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            out.setdefault((kind, fold(cells[3]), fold(cells[4])), set()).add(fold(cells[1]))
    return out


def registry() -> Package:
    done = built()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Azad Jammu and Kashmir, the councils elected in the local bodies elections of 2022, their parties, and the "
        "Election Commission's result pages they come from",
    )
    p.external.add(PAKISTAN)
    p.add("polity", id=AJK, kind="region", iso_code="PK-JK")
    p.name(AJK, "Azad Jammu and Kashmir", "en", "official")
    p.name(AJK, "آزاد جموں و کشمیر", "ur", "native", preferred=False)
    p.add("slug", path="pk/azad-jammu-and-kashmir", entity_id=AJK, is_current=True)
    p.add("polity_parent", polity_id=AJK, parent_id=PAKISTAN)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Azad Jammu and Kashmir Election Commission",
        title="Local Bodies Election 2022: results by ward",
        url=ERMIS_PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Azad Jammu and Kashmir Election Commission",
    )
    stored: set[str] = set()
    for url in _artifacts(done):
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key not in stored:  # two pages with the same bytes are one artifact
            stored.add(artifact.storage_key)
            p.artifact(artifact, SOURCE_ID)
    p.add("electoral_system", id=SYSTEM, polity_id=AJK, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under the Azad Jammu and Kashmir Local Government Act, 1990, each ward of a union council, municipal "
        "corporation, municipal committee, or town committee elects one general member by plurality, and each union "
        "council elects one general member of its district council the same way. Seats for women, peasants, and "
        "technocrats, and the chairmen and vice chairmen, are filled afterwards and are not part of these results.",
        lang="en",
    )
    districts = {district_body(s): district_en(s) for s in done if s.kind == 1}
    for body, name in districts.items():
        p.add("body", id=body, polity_id=AJK, kind="council")
        p.name(body, f"District Council {name}", "en", "official")
    bodies = {council_body(s): body_name(s) for s in done if s.kind != 1}
    for s in done:
        if s.kind == 1:
            bodies.setdefault(council_body(s), body_name(s))
    for body, name in bodies.items():
        p.add("body", id=body, polity_id=AJK, kind="council")
        p.name(body, name, "en", "official")
    codes = {party_code(c.party) for s in done for c in s.candidates} - {"IND", None}
    for code in sorted(c for c in codes if c):
        id = key("party", code)
        english, urdu = PARTIES[code]
        p.add("party", id=id, kind="party", polity_id=AJK)
        p.name(id, english, "en", "official")
        p.name(id, urdu, "ur", "native", preferred=False)
    return p


def geo() -> Package:
    done = built()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The union councils, municipal corporations, municipal committees, and town committees of Azad Jammu and "
        "Kashmir and their wards, as the Election Commission's result pages for the 2022 local bodies elections "
        "name them",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(PAKISTAN)
    p.external.add(AJK)
    p.add(
        "scheme",
        code=COUNCIL_SCHEME,
        polity_id=AJK,
        kind="administrative",
        rank=4,
        noun="local council",
        kinds={
            "union_council": "union council",
            "municipal_corporation": "municipal corporation",
            "municipal_committee": "municipal committee",
            "town_committee": "town committee",
        },
        code_system=None,
    )
    p.add("scheme", code=WARD_SCHEME, polity_id=AJK, kind="electoral_district", rank=5, noun="ward", code_system=None)
    councils: dict[str, Seat] = {}
    wards: dict[str, Seat] = {}
    for s in done:
        councils.setdefault(council_area(s), s)
        if s.kind != 1:
            wards.setdefault(ward_area(s), s)
    for id, s in councils.items():
        kind = "union_council" if s.kind in (1, 2) else KINDS[s.kind]
        p.add("area", id=id, scheme_code=COUNCIL_SCHEME)
        p.add("area_kind", area_id=id, kind=kind, source_id=SOURCE_ID)
        p.name(id, body_name(s), "en", "official")
        p.name(id, s.council, "ur", "native", preferred=False)
    for id, s in wards.items():
        p.add("area", id=id, scheme_code=WARD_SCHEME)
        p.add("area_parent", area_id=id, parent_id=council_area(s), valid_from=POLLS[s.division])
        p.name(id, f"{ward_en(s)}, {body_name(s)}", "en", "official")
        p.name(id, s.ward, "ur", "native", preferred=False)
    return p


def _slug(text: str) -> str:
    return changeset.slug(text)


METHOD = (
    "The result table of the ward (or, for a district council seat, of the union council) as the Azad Jammu and "
    "Kashmir Election Commission's result site shows it: registered voters, votes polled, rejected votes, and each "
    "candidate's votes. {winner} {note}"
)
ERMIS_NOTE = "The figures are those of the Commission's results system for the local bodies elections of 2022."
EC_NOTE = (
    "The figures are those of the Commission's own site for the Muzaffarabad division, whose tables do not always "
    "add up: in many wards the votes polled equal the candidates' votes alone, and in some they are smaller than "
    "the candidates' votes plus the rejected votes. They are stored as printed."
)


def election(division: str) -> Package:
    day = POLLS[division]
    p = Package(
        f"{SOURCE}/{day.isoformat()}",
        SOURCE,
        f"Azad Jammu and Kashmir's local bodies elections in the {division} division on {day.isoformat()}: the "
        "votes of every candidate in every ward and district council seat whose results the Election Commission "
        "publishes",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {PAKISTAN, AJK}
    done = [s for s in built() if s.division == division]
    named = winners(division)
    lists = [winners_url(division, kind) for kind in KINDS if winners_url(division, kind) in raw.keys(SOURCE)]
    for url in {SCHEDULE, NOTIFICATION, *lists} | {s.key for s in done}:
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day.isoformat())
    root = f"pk/{day.isoformat()}-ajk-{division.lower()}-local"
    p.add("election", id=election, polity_id=AJK, kind="local", date_from=day, date_to=day, status="held")
    p.add("slug", path=root, entity_id=election, is_current=True)
    p.name(election, f"{day.year} Azad Jammu and Kashmir local bodies elections, {division} division", "en", "official")
    p.add(
        "note",
        id=key("note", election, "coverage"),
        entity_id=election,
        kind="context",
        body_md=f"The Election Commission polled the {division} division on {day.strftime('%-d %B %Y')} under its "
        "division-wise schedule of 21 November 2022. Only the seats whose result tables on the Commission's result "
        "site hold votes are stored here"
        + (
            ", and the elected candidates are those on the Commission's list of winners for the division."
            if named
            else ". The Commission's site publishes no list of winners for this division."
        ),
        lang="en",
    )
    elected = 0
    paths: set[str] = set()
    values: list[dict[str, object]] = []
    declared: list[tuple[str, str | None, int, int]] = []
    for s in done:
        identity = (s.division, s.district, s.tehsil, s.council, s.kind, s.ward)
        contest = key("contest", *identity)
        district = _slug(district_en(s))
        council = _slug(council_label(s)) or f"council-{s.council_order + 1}-{_slug(s.tehsil_en)}"
        if s.kind == 1:
            body = district_body(s)
            area = council_area(s)
            label = f"District Council {district_en(s)}, {council_label(s)} union council seat"
            path = f"{root}/{district}/district-council/{council}"
            scheme = COUNCIL_SCHEME
            code = f"{day.year}:{s.division}:{s.district}:{s.tehsil}:{s.council}"
        else:
            body = council_body(s)
            area = ward_area(s)
            label = f"{body_name(s)}, {ward_en(s)}"
            path = (
                f"{root}/{district}/{council}/ward-{s.ward_number if s.ward_number is not None else s.ward_order + 1}"
            )
            scheme = WARD_SCHEME
            code = f"{day.year}:{s.division}:{s.district}:{s.tehsil}:{s.council}:{s.ward}"
        n = 1
        base = path
        while path in paths:
            n += 1
            path = f"{base}-{n}"
        paths.add(path)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{day.year} Azad Jammu and Kashmir local bodies elections, {label}", "en", "official")
        p.add("slug", path=path, entity_id=contest, is_current=True)
        unit = key("unit", *identity)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AJK,
            scheme_code=scheme,
            code=code,
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.name(unit, label, "en", "official")
        result_set = key("result_set", *identity)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        winner = (
            "The elected candidate is the one the Commission's list of winners for the division names."
            if named
            else "The site names no winner for the seat, and none is stored."
        )
        note = METHOD.format(winner=winner, note=ERMIS_NOTE if division != "Muzaffarabad" else EC_NOTE)
        if s.tendered:
            note += f" The page also reports {s.tendered} tendered votes, which have no column here."
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=SOURCE_ID,
            locator={"url": s.key},
        )

        def put(option: str | None, measure: int, value: int) -> None:
            values.append(
                {"result_set_id": result_set, "unit_id": unit, "option_id": option, "measure": measure, "value": value}
            )
            declared.append((result_set, option, measure, value))

        put(None, REGISTERED, s.registered)
        put(None, BALLOTS, s.polled)
        put(None, INVALID, s.rejected)
        ordered = sorted(enumerate(s.candidates), key=lambda t: (-t[1].votes, t[0]))
        for position, (_, c) in enumerate(ordered, 1):
            option = key("option", *identity, position)
            person = key("person", *identity, position)
            code_ = party_code(c.party)
            p.add("person", id=person)
            p.name(person, c.name, "ur", "official")
            p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=c.name)
            p.add(
                "candidacy",
                id=key("candidacy", *identity, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=key("party", code_) if code_ not in ("IND", None) else None,
                is_independent=True if code_ == "IND" else None,
            )
            put(option, VOTES, c.votes)
            names = named.get((s.kind, fold(s.district), fold(s.council)), set())
            if fold(c.name) in names and c.votes == s.candidates[ordered[0][0]].votes:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                elected += 1
    if division == "Muzaffarabad":
        p.accept(
            "accounting.turnout",
            "the Commission's site prints more votes polled than registered voters for two wards, and the figures are "
            "stored as printed",
        )
    frame = pl.DataFrame(values, schema_overrides={"option_id": pl.String}).with_columns(
        channel=pl.lit(0, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(0, pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    )
    p.frame("result_value", frame)
    print(division, "elected", elected, "of", len(done), "seats")
    for rs, option, measure, value in declared:
        p.add("declared", result_set_id=rs, option_id=option, measure=measure, value=value)
    return p


def fetch() -> None:
    crawl.fetch()
    crawl.fetch_ec()
    for url in (SCHEDULE, NOTIFICATION, EC_PAGE):
        raw.fetch(SOURCE, url, suffix=".pdf" if url.endswith(".pdf") else ".html")


def parse() -> None:
    for build in (registry, geo):
        print(build().write())
    for division in BUILT:
        print(election(division).write())
