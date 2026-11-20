"""Myanmar's Union Election Commission: the open results portal for the 2017 by-elections, by constituency."""

import csv
import html
import io
import re
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "mm.uec"
SITE = "https://openelectiondata.uec.gov.mm/results2017"
MYANMAR = str(uuid5(package.NAMESPACE, "polity\x1fMM"))
VOTES = 1
DAY = "2017-04-01"
PAGES = {
    "pyithu-my": f"{SITE}/",
    "pyithu-en": f"{SITE}/index-en/index.html",
    "amyotha-my": f"{SITE}/amyotha-mm/index.html",
    "region-my": f"{SITE}/region-mm/index.html",
}
CONSTITUENCY_SCHEME = "mm.constituency"
COUNT_SCHEME = "mm.constituency_count"
STATE_LABELS = {"state", "ပြည်နယ်", "တိုင်း"}
CHAMBERS = {
    "pyithu": ("lower_house", "Pyithu Hluttaw", "House of Representatives"),
    "amyotha": ("upper_house", "Amyotha Hluttaw", "House of Nationalities"),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


UEC = key("source", "results-portal")
SYSTEM = key("electoral_system", "fptp")

METHOD = (
    "Published by the Union Election Commission on its open election data portal for the 2017 by-elections. Each "
    "constituency lists its candidates, their parties, and their votes. The portal gives no electorate, ballots cast, "
    "or rejected-ballot figure for a constituency, and its page says the figures were not final when it was captured."
)


def cached(url: str) -> raw.Artifact:
    """The cached file for a portal URL, found by its tail since the cache may hold an archived copy."""
    tails = [k for k in raw.keys(SOURCE) if k.endswith(url.removeprefix("https://"))]
    if len(tails) != 1:
        raise ValueError(f"{url}: expected one cached copy, found {len(tails)}")
    return raw.cached(SOURCE, tails[0])


def read(url: str) -> str:
    return cached(url).path.read_text(encoding="utf-8")


@dataclass
class Candidate:
    name: str
    party: str
    votes: int
    elected: bool


@dataclass
class Seat:
    chamber: str
    code: str
    name: str
    state: str
    district: str
    csv: str
    candidates: list[Candidate] = field(default_factory=list)
    english: Seat | None = None


TOKEN = re.compile(
    r"<a class='collapsible location'>(?P<head>.*?)<small class='label'>(?P<label>[^<]*)</small>\s*</a>"
    r"|<strong class='location'>(?P<unit>[^<]*)</strong>"
    r"|href='(?:\.\./)?resources/data/results-(?P<chamber>[a-z]+)-(?P<slug>[a-z0-9]+)-(?:mm|en)\.csv'"
    r"|<span class='candidate'>(?P<who>[^<]*?)\s*(?P<win><small class='label winning'>[^<]*</small>)?\s*"
    r"<span>(?:Party|ပါတီ):\s*(?P<party>[^<]*?)\s*</span></span>\s*<span class='votes'><strong>(?P<votes>\d+)</strong>",
    re.DOTALL,
)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text).replace("\u200b", "")).strip()


def seats(text: str) -> list[Seat]:
    out: list[Seat] = []
    state = district = unit = ""
    seat: Seat | None = None
    for m in TOKEN.finditer(text):
        if m["head"] is not None:
            if m["label"] in STATE_LABELS:
                state, district, unit = clean(m["head"]), "", ""
            else:
                district, unit = clean(m["head"]), ""
        elif m["unit"] is not None:
            unit = clean(m["unit"])
        elif m["slug"] is not None:
            seat = Seat(m["chamber"], f"{m['chamber']}-{m['slug']}", unit or district, state, district, m["slug"])
            out.append(seat)
        elif seat is not None:
            seat.candidates.append(Candidate(clean(m["who"]), clean(m["party"]), int(m["votes"]), m["win"] is not None))
    return out


def load() -> dict[str, list[Seat]]:
    return {name: seats(read(url)) for name, url in PAGES.items()}


STATES = {
    "ကယားပြည်နယ်": ("kayah", "Kayah State"),
    "ချင်းပြည်နယ်": ("chin", "Chin State"),
    "မွန်ပြည်နယ်": ("mon", "Mon State"),
    "ရခိုင်ပြည်နယ်": ("rakhine", "Rakhine State"),
    "စစ်ကိုင်းတိုင်းဒေသကြီး": ("sagaing", "Sagaing Region"),
    "ရှမ်းပြည်နယ်": ("shan", "Shan State"),
    "ပဲခူးတိုင်းဒေသကြီး": ("bago", "Bago Region"),
    "ရန်ကုန်တိုင်းဒေသကြီး": ("yangon", "Yangon Region"),
}
# The English page spells one slug differently from the Burmese pages.
ALIAS = {"pyithu-hlaingthayar": "pyithu-hlainthayar"}
REGION_NAMES = {
    "hpruso1": "Hpruso 1",
    "kyethi1": "Kyethi 1",
    "kyethi2": "Kyethi 2",
    "monghsu1": "Monghsu 1",
    "monghsu2": "Monghsu 2",
    "nyaungshwe1": "Nyaungshwe 1",
    "kengtung": "Kengtung 2",
}
STATE_BODIES = {"kayah": "Kayah State Hluttaw", "shan": "Shan State Hluttaw"}
TYPOS = (("အမျိုးသာ(?!း)", "အမျိုးသား"), ("တိုးတရေး", "တိုးတက်ရေး"), ("တစ်သီပုဂ္ဂလ", "တစ်သီးပုဂ္ဂလ"), ("ဒီမိုကရေတစ်", "ဒီမိုကရေစီ"))


def norm(party: str) -> str:
    """Spelling-insensitive form of a Burmese party name: the portal spells one party several ways."""
    text = re.sub(r"[\s\u200b\u1037]", "", party)
    for pattern, fix in TYPOS:
        text = re.sub(pattern, fix, text)
    text = text.replace("ဖွံဖြိုးတိုးတက်ရေး", "ဖွံဖြိုးရေး") if text.startswith("ပြည်ထောင်စုကြံ") else text
    return text.removesuffix("ပါတီ")


PARTIES = {
    norm(name): (code, english)
    for name, code, english in (
        ("ပြည်ထောင်စုကြံခိုင်ရေးနှင့်ဖွံ့ဖြိုးရေးပါတီ", "usdp", "Union Solidarity and Development Party"),
        ("အမျိုးသားဒီမိုကရေစီအဖွဲ့ချုပ်ပါတီ", "nld", "National League for Democracy"),
        ("အမျိုးသားဒီမိုကရေစီအင်အားစုပါတီ", "ndf", "National Democratic Force"),
        ("အမျိုးသားတိုးတက်ရေးပါတီ", "ndp", "National Development Party"),
        ("တိုင်းရင်းသားစည်းလုံးညီညွတ်ရေးပါတီ", "nup", "National Unity Party"),
        ("ရခိုင်အမျိုးသားပါတီ", "anp", "Arakan National Party"),
        ("ဒီမိုကရက်တစ်ပါတီ(မြန်မာ)", "dpm", "Democratic Party (Myanmar)"),
        ("ပြည်သူ့ဒီမိုကရေစီပါတီ", "pdp", "People's Democracy Party"),
        ("ပြည်သူ့အကျိုးပြု ကျောင်းသားများ ဒီမိုကရေစီပါတီ", "pcsdp", "Public Contribute Students Democracy Party"),
        ("ဖလုံ-ဖဝေါ်ဒီမိုကရက်တစ်ပါတီ", "psdp", "Phalon-Sawaw Democratic Party"),
        ("မြန်မာနိုင်ငံတောင်သူလယ်သမားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", "mfdp", "Myanmar Farmers Development Party"),
        ("မြန်မာအမျိုးသားကွန်ကရက်ပါတီ", "mncp", "Myanmar National Congress Party"),
        ("မွန်ဒေသလုံးဆိုင်ရာဒီမိုကရေစီပါတီ", "amrdp", "All Mon Regions Democracy Party"),
        ("မွန်အမျိုးသားပါတီ", "mnp", "Mon National Party"),
        ("လူဘောင်သစ်ပါတီ", "nsp", "New Society Party"),
        ("ရှမ်းတိုင်းရင်းသားများဒီမိုကရေစီအဖွဲ့ချုပ်ပါတီ", "snld", "Shan Nationalities League for Democracy"),
        ("ရှမ်းတိုင်းရင်းသားများဒီမိုကရက်တစ်ပါတီ", "sndp", "Shan Nationalities Democratic Party"),
        ("လားဟူအမျိုးသားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", "lndp", "Lahu National Development Party"),
        ("အင်းအမျိုးသားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", "indp", "Inn National Development Party"),
        ("အာခါအမျိုးသားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", "andp", "Akha National Development Party"),
        ("ချင်းအမျိုးသားဒီမိုကရေစီပါတီ", "cndp", "Chin National Democratic Party"),
        ("လူမျိုးပေါင်းစုံဒီမိုကရေစီပါတီ", "amdp", None),
        ("ပြည်ထောင်စုလယ်သမားအလုပ်သမားအင်အားစုပါတီ", "uflp", None),
        ('"ဝ"လစ်ဘရယ်ဒီမိုကရက်တစ်တိုးတက်ဖွံ့ဖြိုးရေးပါတီ', "wldp", None),
    )
}
INDEPENDENT = norm("တစ်သီးပုဂ္ဂလ")


def party(name: str) -> tuple[str, str | None] | None:
    n = norm(name)
    if n == INDEPENDENT:
        return None
    if n not in PARTIES:
        raise ValueError(f"unknown party {name!r}")
    return PARTIES[n]


DIGITS = str.maketrans("၀၁၂၃၄၅၆၇၈၉", "0123456789")


def checked() -> dict[str, Seat]:
    """Every seat from the Burmese pages, with the English page's names, checked against each cached CSV."""
    pages = load()
    english = {ALIAS.get(s.code, s.code): s for s in pages["pyithu-en"]}
    out: dict[str, Seat] = {}
    for name in ("pyithu-my", "amyotha-my", "region-my"):
        for seat in pages[name]:
            seat.code = ALIAS.get(seat.code, seat.code)
            if seat.code in out:
                raise ValueError(f"{seat.code} listed twice")
            out[seat.code] = seat
    for code, seat in out.items():
        top = max(c.votes for c in seat.candidates)
        if [c.votes for c in seat.candidates if c.elected] != [top]:
            raise ValueError(f"{code}: the flagged winner is not the sole top vote-getter")
        if code in english:
            seat.english = english[code]
    for link in sorted(raw.keys(SOURCE)):
        found = re.search(r"results-([a-z]+-[a-z0-9]+)-(?:mm|en)\.csv$", link)
        if not found:
            continue
        code = ALIAS.get(found[1], found[1])
        rows = list(csv.DictReader(io.StringIO(raw.cached(SOURCE, link).path.read_text(encoding="utf-8"))))
        if sorted(int(r["Votes"]) for r in rows) != sorted(c.votes for c in out[code].candidates):
            raise ValueError(f"{code}: {link} disagrees with the portal page")
    return out


def pair(seat: Seat) -> list[Candidate | None]:
    """The English page's candidate for each Burmese-page candidate, matched by votes; its order differs."""
    if seat.english is None:
        return [None] * len(seat.candidates)
    left = list(seat.english.candidates)
    matched: list[Candidate | None] = []
    for c in seat.candidates:
        hit = next((e for e in left if e.votes == c.votes), None)
        if hit:
            left.remove(hit)
        matched.append(hit)
    gaps = [i for i, hit in enumerate(matched) if hit is None]
    if len(gaps) != len(left) or len(gaps) > 1:
        raise ValueError(f"{seat.code}: candidates do not pair across the pages")
    for i in gaps:
        matched[i] = left[0]
    return matched


# Packages --------------------------------------------------------------------------------------------------------


def fetch() -> None:
    from ea.sources.mm import uec_general

    uec_general.fetch()
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for seat in checked().values():
        for lang in ("mm", "en"):
            chamber, name = seat.code.split("-", 1)
            if seat.code == "pyithu-hlainthayar":
                name = "hlaingthayar" if lang == "en" else name
            raw.fetch(SOURCE, f"{SITE}/resources/data/results-{chamber}-{name}-{lang}.csv", suffix=".csv")


def packages() -> list[str]:
    from ea.sources.mm import uec_general

    return [f"{SOURCE}/registry", f"{SOURCE}/{DAY}", *uec_general.packages()]


def parse() -> None:
    from ea.sources.mm import uec_general

    registry().write()
    build().write()
    uec_general.parse()


def emit() -> None:
    package.run("load", packages())


def body_key(seat: Seat) -> str:
    if seat.chamber != "region":
        return key("body", seat.chamber)
    return key("body", "state", STATES[seat.state][0])


def area_key(seat: Seat) -> str:
    return key("area", seat.code)


def party_key(code: str) -> str:
    return key("party", code)


def english_name(seat: Seat) -> str:
    state = STATES[seat.state][1]
    number = seat.name.translate(DIGITS).rsplit(" ", 1)[-1]
    if seat.chamber == "amyotha":
        return f"{state} constituency {number}"
    if seat.chamber == "region":
        return f"{REGION_NAMES[seat.code.split('-', 1)[1]]} constituency"
    return f"{PYITHU_ENGLISH[seat.code]} constituency"


PYITHU_ENGLISH = {
    "pyithu-chaungzon": "Chaungzon",
    "pyithu-ann": "Ann",
    "pyithu-monywa": "Monywa",
    "pyithu-kyethi": "Kyethi",
    "pyithu-mongshu": "Monghsu",
    "pyithu-hlainthayar": "Hlaingthayar",
    "pyithu-kawhmu": "Kawhmu",
    "pyithu-dagonseikkan": "Dagon Seikkan",
    "pyithu-dagoneast": "Dagon East",
}


def body_name(seat: Seat) -> str:
    if seat.chamber == "region":
        return STATE_BODIES[STATES[seat.state][0]]
    return CHAMBERS[seat.chamber][1]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Myanmar's constituencies, parties, and legislatures that held the 2017 by-elections, from the Union "
        "Election Commission's results portal",
    )
    p.external.add(MYANMAR)
    p.add(
        "source",
        id=UEC,
        kind="official_body",
        publisher="Union Election Commission of Myanmar",
        title="Myanmar by-election 2017 results",
        url=f"{SITE}/",
        license="Official publication; reuse terms are not stated",
        attribution="Union Election Commission of Myanmar",
    )
    for code, kind, rank, noun in (
        (CONSTITUENCY_SCHEME, "electoral_district", 1, "constituency"),
        (COUNT_SCHEME, "synthetic", 2, "constituency count"),
    ):
        p.add("scheme", code=code, polity_id=MYANMAR, kind=kind, rank=rank, noun=noun)
    seats = checked()
    for chamber, (kind, english, _) in CHAMBERS.items():
        id = key("body", chamber)
        p.add("body", id=id, polity_id=MYANMAR, kind=kind)
        p.name(id, english, "en", "official")
    for state in sorted({STATES[s.state][0] for s in seats.values() if s.chamber == "region"}):
        id = key("body", "state", state)
        p.add("body", id=id, polity_id=MYANMAR, kind="unicameral")
        p.name(id, STATE_BODIES[state], "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=MYANMAR, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each constituency elects one member, and the candidate with the most votes is elected.",
        lang="en",
    )
    parties: dict[str, tuple[str, str | None]] = {}
    burmese: dict[str, str] = {}
    for seat in seats.values():
        for c in seat.candidates:
            if found := party(c.party):
                parties[found[0]] = found
                burmese.setdefault(found[0], c.party)
    for code, (_, english) in sorted(parties.items()):
        id = party_key(code)
        p.add("party", id=id, kind="party", polity_id=MYANMAR)
        p.name(id, burmese[code], "my", "official")
        if english:
            p.name(id, english, "en", "official")
    for seat in sorted(seats.values(), key=lambda s: s.code):
        id = area_key(seat)
        p.add("area", id=id, scheme_code=CONSTITUENCY_SCHEME)
        p.add("area_code", area_id=id, code=seat.code)
        p.name(id, seat.name, "my", "official")
        p.name(id, english_name(seat), "en", "official")
    for url in PAGES.values():
        p.artifact(cached(url), UEC)
    return p


def build() -> Package:
    seats = checked()
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 2017 Myanmar by-elections for the Pyithu Hluttaw, the Amyotha Hluttaw, and the Kayah and Shan state "
        "Hluttaws, with every candidate's votes in each constituency, from the Union Election Commission",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(MYANMAR)
    for url in PAGES.values():
        p.uses(cached(url))
    for link in sorted(raw.keys(SOURCE)):
        if link.endswith(".csv"):
            p.uses(raw.cached(SOURCE, link))
    election = key("election", DAY)
    p.add(
        "election",
        id=election,
        polity_id=MYANMAR,
        kind="by_election",
        date_from=date.fromisoformat(DAY),
        date_to=date.fromisoformat(DAY),
        date_precision="day",
        status="held",
    )
    p.name(election, "2017 Myanmar by-elections", "en", "official")
    p.add("slug", path=f"mm/{DAY}-by-election", entity_id=election, is_current=True)
    for seat in sorted(seats.values(), key=lambda s: s.code):
        code = seat.code
        contest = key("contest", DAY, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body_key(seat),
            district_area_id=area_key(seat),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{body_name(seat)}, {english_name(seat)}", "en", "official")
        p.add("slug", path=f"mm/{DAY}-by-election/{code}", entity_id=contest, is_current=True)
        english = pair(seat)
        options: list[str] = []
        for position, (c, e) in enumerate(zip(seat.candidates, english, strict=True)):
            found = party(c.party)
            option = key("option", DAY, code, position)
            person = key("person", DAY, code, position)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_key(found[0]) if found else None,
                ballot_label=c.name,
            )
            p.add("person", id=person)
            p.name(person, c.name, "my", "official")
            if e:
                p.name(person, e.name, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", DAY, code, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=party_key(found[0]) if found else None,
                is_independent=found is None,
            )
        note = METHOD
        if code == "pyithu-chaungzon":
            note += (
                " The portal's English page prints 12,636 votes for the National League for Democracy candidate, "
                "while its Burmese page and the constituency's CSV print 12,363; the stored figure follows the CSV."
            )
        rs = key("result_set", DAY, code)
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=UEC,
            status="active",
            is_default=True,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=note, lang="en")
        unit = key("unit", DAY, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MYANMAR,
            scheme_code=COUNT_SCHEME,
            code=f"{DAY}:{code}",
            area_relation="equals",
            area_id=area_key(seat),
            kind="standard",
        )
        p.name(unit, f"{english_name(seat)} count", "en", "official")
        for option, c in zip(options, seat.candidates, strict=True):
            p.add(
                "result_value",
                result_set_id=rs,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=c.votes,
            )
            p.add(
                "outcome",
                result_set_id=rs,
                option_id=option,
                kind="elected" if c.elected else "lost",
                seats=1 if c.elected else None,
            )
    return p
