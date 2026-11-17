"""Hungary's by-elections (időközi választások) from the National Election Office (Nemzeti Választási Iroda).

Local and nationality by-elections held since December 2025, and the parliamentary by-elections, come from the office's
results service (vtr.valasztas.hu), whose per-settlement polling-station files are crawled once per service and
election day and kept unchanged in one zip each. Earlier local, nationality, and parliamentary by-elections come from
the office's archive of per-election workbooks.
"""

import functools
import html
import io
import json
import re
import urllib.parse
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast
from uuid import uuid5

import fastexcel
import httpx

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.hu import valasztas as hv

SOURCE = "hu.idokozi"
PAGE = "https://www.valasztas.hu/idokozi-valasztasok"
VTR = "https://vtr.valasztas.hu"
SERVICES = ("ionk", "inemz")
TODAY = date(2026, 11, 16)


def list_url(service: str) -> str:
    return f"{VTR}/{service}/data/ValleirtLista.json"


def crawl_url(service: str, vl_id: int) -> str:
    return f"{VTR}/{service}/data/{vl_id}#crawl"


def load_list(service: str) -> list[dict[str, object]]:
    document = json.loads(raw.cached(SOURCE, list_url(service)).path.read_bytes())
    return cast(list[dict[str, object]], document["list"])


@dataclass(frozen=True)
class Event:
    """One election at one settlement (and for nationality elections, one nationality)."""

    service: str
    vl_id: int
    day: date
    maz: str
    taz: str
    nemzkod: str | None

    @property
    def folder(self) -> str:
        return f"{self.vl_id}/{self.maz}/{self.taz}" + (f"/{self.nemzkod}" if self.nemzkod else "")


def events(service: str) -> list[Event]:
    found: dict[Event, None] = {}
    for row in load_list(service):
        day = date.fromisoformat(str(row["valdat"])[:10])
        if day >= TODAY:
            continue
        found[
            Event(
                service,
                int(cast(int, row["vl_id"])),
                day,
                str(row["maz"]),
                str(row["taz"]),
                str(row["nemzkod"]) if service == "inemz" else None,
            )
        ] = None
    return list(found)


def get(url: str) -> bytes | None:
    for _ in range(5):
        try:
            response = httpx.get(url, headers={"User-Agent": raw.USER_AGENT}, timeout=120, follow_redirects=True)
        except httpx.TransportError:
            continue
        if response.status_code == 404 or response.headers.get("content-type", "").startswith("text/html"):
            return None
        response.raise_for_status()
        return response.content
    raise RuntimeError(f"{url} failed five times")


def crawl(service: str, vl_id: int, group: list[Event]) -> None:
    """Store every file of one election day's settlements, as the service served them, in one zip."""
    if crawl_url(service, vl_id) in raw.keys(SOURCE):
        return
    base = f"{VTR}/{service}/data"
    files: dict[str, bytes] = {}

    def keep(path: str) -> bytes | None:
        content = get(f"{base}/{path}")
        if content is not None:
            files[path] = content
        return content

    configs: dict[str, str] = {}
    for part in ("ver", "szavossz"):
        content = keep(f"{vl_id}/{part}_config.json")
        if content is None:
            print(f"{service} {vl_id}: no {part} configuration")
            return
        configs[part] = str(json.loads(content)["version"])
    for event in group:
        a = f"{event.folder}"
        keep(f"{a}/Valleirt.json")
        keep(f"{a}/ver/{configs['ver']}/Jelolesek.json")
        keep(f"{a}/ver/{configs['ver']}/Alapadatok.json")
        keep(f"{a}/szavossz/{configs['szavossz']}/SzavkorJkv-{event.maz}-{event.taz}.json")
        keep(f"{a}/szavossz/{configs['szavossz']}/SzeredmTelep-{event.maz}-{event.taz}.json")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    raw.store(
        SOURCE,
        crawl_url(service, vl_id),
        buffer.getvalue(),
        suffix=".zip",
        retrieved_at=datetime.now(UTC),
        media_type="application/zip",
    )


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for service in SERVICES:
        raw.fetch(SOURCE, list_url(service), suffix=".json")
        by_day: dict[int, list[Event]] = {}
        for event in events(service):
            by_day.setdefault(event.vl_id, []).append(event)
        for vl_id, group in sorted(by_day.items()):
            crawl(service, vl_id, group)
            print(f"{service} {vl_id}: {len(group)} elections")
    fetch_archive()
    from ea.sources.hu import idokozi_archive

    idokozi_archive.fetch()


# ---------------------------------------------------------------------------------------------------------------
# The office's archive of per-election workbooks

PORTAL = "https://portal-data.valasztas.hu/idokozi"
ARCHIVE_PAGES = {
    "local": "https://www.valasztas.hu/helyi-onkormanyzati-valasztasok",
    "nationality": "https://www.valasztas.hu/idokozi-nemzetisegi-onkormanyzati-valasztasok",
    "parliament": "https://www.valasztas.hu/orszaggyulesi-valasztasok1",
}
SETTLEMENTS = "https://vtr.valasztas.hu/ogy2026/data/04112100/ver/Telepulesek.json"
HUNGARIAN_MONTHS = {
    "január": 1,
    "február": 2,
    "március": 3,
    "április": 4,
    "május": 5,
    "június": 6,
    "július": 7,
    "augusztus": 8,
    "szeptember": 9,
    "október": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True)
class Card:
    """One by-election as the office's archive page lists it, with its workbook."""

    category: str
    day: date
    kind: str
    place: str
    county: str
    maz: str | None
    taz: str | None
    reason: str
    url: str | None


def flat(page: str) -> str:
    text = re.sub(r'<a [^>]*href="([^"]*)"[^>]*>', lambda m: " [[LINK " + html.unescape(m.group(1)) + "]] ", page)
    text = re.sub(r"<[^>]+>", " | ", text)
    return re.sub(r"(\| )+", "| ", html.unescape(re.sub(r"\s+", " ", text)))


def hungarian_day(text: str) -> date:
    found = re.match(r"(\d{4})\. (\w+) (\d+)\.", text)
    if found is None:
        raise ValueError(f"no date in {text!r}")
    return date(int(found[1]), HUNGARIAN_MONTHS[found[2]], int(found[3]))


def cards(category: str) -> list[Card]:
    text = flat(raw.cached(SOURCE, ARCHIVE_PAGES[category]).path.read_text())
    found: list[Card] = []
    pattern = re.compile(
        r"\| (\d{4}\. [^|]+?) \| Választás típusa: \| ([^|]+?) \| Település, megye: \| (?:\[\[LINK ([^\]]+)\]\] )?"
        r"([^|]*?) \| Választás oka: \| ([^|]*?) \|"
    )
    for block in text.split("Szavazás napja:")[1:]:
        match = pattern.match(block.lstrip())
        if match is None:
            continue
        day = hungarian_day(match[1])
        link = match[3] or ""
        ids = dict(re.findall(r"(maz|taz|evk)=(\d+)", link))
        place, _, county = match[4].rpartition(", ")
        own = re.findall(r"\[\[LINK ([^\]]+\.xls)\]\] Letöltés", block)
        found.append(
            Card(
                category,
                day,
                match[2].strip(),
                place,
                county,
                ids.get("maz"),
                ids.get("taz"),
                match[5].strip(),
                own[0] if own else None,
            )
        )
    return found


def workbook_url(url: str) -> str:
    return urllib.parse.quote(url, safe=":/?&=#%")


def crawl_archive(category: str, day: date, group: list[Card]) -> None:
    url = f"{PORTAL}/{category}/{day}#crawl"
    if url in raw.keys(SOURCE):
        return
    files: dict[str, bytes] = {}
    for card in group:
        if card.url is None or card.url in files:
            continue
        for _ in range(5):
            try:
                response = httpx.get(
                    workbook_url(card.url), headers={"User-Agent": raw.USER_AGENT}, timeout=120, follow_redirects=True
                )
            except httpx.TransportError:
                continue
            response.raise_for_status()
            files[card.url] = response.content
            break
        else:
            raise RuntimeError(f"{card.url} failed five times")
    if not files:
        return
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name.removeprefix(PORTAL + "/"), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    raw.store(
        SOURCE,
        url,
        buffer.getvalue(),
        suffix=".zip",
        retrieved_at=datetime.now(UTC),
        media_type="application/zip",
    )


def fetch_archive() -> None:
    raw.fetch(SOURCE, SETTLEMENTS, suffix=".json")
    for category, page in ARCHIVE_PAGES.items():
        raw.fetch(SOURCE, page, suffix=".html")
    for category in ARCHIVE_PAGES:
        by_day: dict[date, list[Card]] = defaultdict(list)
        for card in cards(category):
            by_day[card.day].append(card)
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda item: crawl_archive(category, item[0], item[1]), sorted(by_day.items())))
        print(
            f"{category}: {sum(c.url is not None for g in by_day.values() for c in g)} workbooks on {len(by_day)} dates"
        )


# ---------------------------------------------------------------------------------------------------------------
# Building one election day


HUNGARY = hv.HUNGARY
TERRITORY = hv.TERRITORY


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NVI = key("source", SOURCE)
PARLIAMENT = key("body", "parliament")
LOCAL_MAYOR = key("electoral_system", "local-mayor")
LOCAL_BLOCK = key("electoral_system", "local-block")
LOCAL_SMD = key("electoral_system", "local-smd")
NATIONALITY_BLOCK = key("electoral_system", "nationality-block")
SMD_FPTP = key("electoral_system", "smd", 2014)
SYSTEMS: list[tuple[str, date, dict[str, object], str]] = [
    (
        LOCAL_MAYOR,
        date(1990, 1, 1),
        {"formula": "fptp"},
        "Mayors are elected directly in a single round; the candidate with the most votes wins.",
    ),
    (
        LOCAL_BLOCK,
        date(1990, 1, 1),
        {"formula": "block"},
        (
            "In settlements of up to 10,000 inhabitants the council is elected on one ballot from individual candidates: "
            "each voter may vote for as many candidates as there are seats, and the candidates with the most votes win."
        ),
    ),
    (
        LOCAL_SMD,
        date(1990, 1, 1),
        {"formula": "fptp"},
        (
            "In settlements of more than 10,000 inhabitants and in Budapest's districts, part of the council is elected in "
            "single-member wards by the most votes."
        ),
    ),
    (
        NATIONALITY_BLOCK,
        date(2010, 1, 1),
        {"formula": "block"},
        (
            "A settlement's nationality self-government is elected by the voters on the nationality's roll, each voting "
            "for as many candidates as there are seats; the candidates with the most votes win."
        ),
    ),
    (
        SMD_FPTP,
        date(2014, 1, 1),
        {"formula": "fptp"},
        "A parliamentary by-election in a single-member district is decided by the most votes in one round.",
    ),
]
METHOD_NATIONALITY = (
    "Counted by the polling-station commission and published by the National Election Office (Nemzeti Választási "
    "Iroda) for the settlement as a whole (the nationality's roll is voted at one polling station). Registered voters "
    "are the nationality's roll, voters are those who signed it, ballots cast are the stamped ballots in the box, and "
    "valid and invalid are the valid and invalid ballots."
)
MONTHS = [
    "január",
    "február",
    "március",
    "április",
    "május",
    "június",
    "július",
    "augusztus",
    "szeptember",
    "október",
    "november",
    "december",
]


@dataclass
class Registry:
    places: dict[str, tuple[str, str]]
    counties: set[str]
    parties: dict[str, str]
    bodies: dict[str, tuple[str, str, str]]
    offices: dict[str, tuple[str, str]]
    districts: dict[str, tuple[str, str, str]]
    cited: set[str]


STATE = Registry({}, set(), {}, {}, {}, {}, set())


def place(code: str, name: str) -> str:
    """The area of a settlement, by the office's county and settlement code."""
    STATE.places.setdefault(code, (code[:2], name))
    STATE.counties.add(code[:2])
    return key("area", "settlement", code)


def county(maz: str) -> str:
    STATE.counties.add(maz)
    return key("area", "county", maz)


def party_id(label: str | None) -> str | None:
    if label is None:
        return None
    label = hv.party_label(label)
    if hv.INDEPENDENT.match(label):
        return None
    folded = label.upper()
    STATE.parties.setdefault(folded, label)
    return key("party", folded)


def council_body(code: str, name: str, nation: str | None = None) -> str:
    if nation is None:
        id = key("body", "council", code)
        STATE.bodies[id] = (f"{name} council", f"{name} képviselő-testülete", "council")
    else:
        id = key("body", "nationality", code, nation)
        STATE.bodies[id] = (
            f"{nation} self-government of {name}",
            f"{name} {nation.lower()} önkormányzat",
            "council",
        )
    return id


def mayor_office(code: str, name: str) -> str:
    id = key("office", "mayor", code)
    STATE.offices[id] = (f"Mayor of {name}", f"{name} polgármestere")
    return id


class Day(hv.Day):
    """One package: every by-election the office held on one date."""

    def __init__(self, day: date, summary: str) -> None:
        self.day = day
        self.p = Package(f"{SOURCE}/{day}", SOURCE, summary, depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"])
        self.p.external |= {HUNGARY, TERRITORY}
        self.values = {}
        self.units = {}
        self.options = {}
        self.sets = {}
        self.declared = {}
        self.outcomes = set()
        self.people = {}
        self.seen = set()
        self.inherited = set()
        self.reasons = {}
        self.cited = set()
        self.elections = set()
        self.contest_ids = {}
        self.slugs: dict[str, str] = {}

    def cite(self, name: str) -> None:
        if name not in self.cited:
            self.cited.add(name)
            STATE.cited.add(name)
            self.p.uses(raw.cached(SOURCE, name))

    def contest(  # type: ignore[override]
        self,
        tag: str,
        election: str,
        kind: str,
        name: str,
        path: str,
        *,
        district_area: str = TERRITORY,
        magnitude: int | None = 1,
        round_number: int = 1,
        previous: str | None = None,
        system: str | None = None,
        body: str | None = None,
        office: str | None = None,
        method: str,
        english: str | None = None,
    ) -> str:
        contest = hv.key("contest", tag)
        self.p.add(
            "contest",
            id=contest,
            election_id=election,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district_area,
            magnitude=magnitude,
            round_number=round_number,
            previous_round_id=None,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        self.p.name(contest, name, "hu", "official")
        if english:
            self.p.name(contest, english, "en", "english", False)
        self.p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = hv.key("result_set", tag)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NVI,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        self.p.add(
            "note", id=hv.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
        )
        self.sets[tag] = result_set
        return result_set

    def option(  # type: ignore[override]
        self,
        tag: str,
        label: str,
        kind: str,
        *,
        nominator: str | None = None,
        position: int | None = None,
        ident: str | None = None,
    ) -> str:
        index = (tag, ident or hv.same_name(label) or label)
        found = self.options.get(index)
        if found is not None:
            return found
        id = hv.key("option", tag, index[1])
        self.p.add(
            "contest_option",
            id=id,
            contest_id=hv.key("contest", tag),
            kind=kind,
            party_id=party_id(nominator) if kind != "answer" else None,
            ballot_position=position,
            ballot_label=re.sub(r"\s+", " ", label).strip(),
        )
        self.options[index] = id
        return id

    def candidate(  # type: ignore[override]
        self,
        tag: str,
        option: str,
        person: str,
        name: str,
        *,
        role: str = "head",
        position: int | None = None,
        nominator: str | None = None,
    ) -> str:
        person_id = self.people.get(person)
        if person_id is None:
            person_id = self.people[person] = hv.key("person", f"ik:{person}")
            self.p.add("person", id=person_id)
            self.p.name(person_id, hv.person_text(hv.TITLES.sub("", name.strip())), "hu", "official")
        candidacy = hv.key("candidacy", tag, option, person)
        self.p.add(
            "candidacy",
            id=candidacy,
            person_id=person_id,
            option_id=option,
            role=role,
            list_position=position,
            ballot_name=re.sub(r"\s+", " ", name).strip(),
            member_of_party_id=None,
            is_independent=nominator is not None and party_id(nominator) is None,
        )
        return candidacy

    def unit(self, area_code: str, number: str | int, kind: str = "standard", relation: str = "within") -> str:
        code = (
            f"{self.day}:{area_code}:{int(number):03d}" if str(number).isdigit() else f"{self.day}:{area_code}:{number}"
        )
        found = self.units.get(code)
        if found is None:
            found = self.units[code] = hv.key("unit", "ik", code)
            self.p.add(
                "reporting_unit",
                id=found,
                polity_id=HUNGARY,
                scheme_code="hu.idokozi_szavazokor",
                code=code,
                area_relation=relation,
                area_id=key("area", "settlement", area_code),
                kind=kind,
            )
        return found

    def settlement_unit(self, area_code: str) -> str:
        code = f"{self.day}:{area_code}"
        found = self.units.get(code)
        if found is None:
            found = self.units[code] = hv.key("unit", "ik", code)
            self.p.add(
                "reporting_unit",
                id=found,
                polity_id=HUNGARY,
                scheme_code="hu.idokozi_telepules",
                code=code,
                area_relation="equals",
                area_id=key("area", "settlement", area_code),
                kind="standard",
            )
        return found


def hungarian_date(day: date) -> str:
    return f"{day.year}. {MONTHS[day.month - 1]} {day.day}."


class Tree:
    """The election of one date and its children, one for each kind of by-election."""

    def __init__(self, d: Day) -> None:
        self.d = d
        self.path = f"hu/{d.day}-by-election"
        english = f"{d.day:%B} {d.day.day}, {d.day.year}"
        self.root = d.election(
            f"ik:{d.day}",
            "by_election",
            f"Hungarian by-elections, {english}",
            f"Időközi választások, {hungarian_date(d.day)}",
            self.path,
        )
        self.children: dict[str, str] = {}

    def child(self, kind: str) -> tuple[str, str]:
        names = {
            "local": ("Local by-elections", "Időközi helyi önkormányzati választások"),
            "nationality": (
                "Nationality self-government by-elections",
                "Időközi nemzetiségi önkormányzati választások",
            ),
            "parliament": ("Parliamentary by-elections", "Időközi országgyűlési képviselő-választások"),
        }[kind]
        path = f"{self.path}/{kind}"
        if kind not in self.children:
            english = f"{names[0]}, {self.d.day:%B} {self.d.day.day}, {self.d.day.year}"
            self.children[kind] = self.d.election(
                f"ik:{self.d.day}:{kind}",
                "by_election",
                english,
                f"{names[1]}, {hungarian_date(self.d.day)}",
                path,
                parent=self.root,
            )
        return self.children[kind], path

    def slug(self, kind: str, text: str, code: str) -> str:
        _, path = self.child(kind)
        found = changeset.slug(text)
        owner = self.d.slugs.setdefault(f"{path}/{found}", code[:5])
        if owner != code[:5]:
            found = f"{found}-{code[:5]}"
            self.d.slugs[f"{path}/{found}"] = code[:5]
        return f"{path}/{found}"


# ---------------------------------------------------------------------------------------------------------------
# The results service


def read_crawl(service: str, vl_id: int) -> dict[str, object] | None:
    url = crawl_url(service, vl_id)
    if url not in raw.keys(SOURCE):
        return None
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        return {name: json.loads(archive.read(name)) for name in archive.namelist()}


def items(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value or [])


def number(value: object) -> int | None:
    return None if value is None else int(cast(int, value))


def nominators(entry: dict[str, object], names: dict[int, str]) -> str:
    codes = [
        int(cast(int, c)) for c in cast(list[object], entry.get("szKodok") or entry.get("jelolo_szervezetek") or [])
    ]
    labels = [names[c] for c in codes if c in names and c != 0]
    return ", ".join(labels) if labels else "Független"


LOCAL_KINDS = {"1": "mayor", "3": "council", "4": "ward"}


def local_contest(d: Day, tree: Tree, role: str, code: str, name: str, ward: str | None, elected: int) -> str:
    tag = f"ik:{d.day}:local:{role}:{code}" + (f":{ward}" if ward else "")
    if tag in d.sets:
        return tag
    election, _ = tree.child("local")
    if role == "mayor":
        title, english = f"{name} polgármester", f"Mayor of {name}"
        office, body, system, kind, magnitude = mayor_office(code, name), None, LOCAL_MAYOR, "office", 1
        slug = tree.slug("local", f"{name}-mayor", code)
    elif role == "council":
        title, english = f"{name} képviselő-testület", f"{name} council"
        office, body, system, kind, magnitude = (
            None,
            council_body(code, name),
            LOCAL_BLOCK,
            "body_seats",
            max(elected, 1),
        )
        slug = tree.slug("local", f"{name}-council", code)
    else:
        title = f"{name} {int(cast(str, ward))}. egyéni választókerület"
        english = f"{name} ward {int(cast(str, ward))}"
        office, body, system, kind, magnitude = None, council_body(code, name), LOCAL_SMD, "body_seats", 1
        slug = tree.slug("local", f"{name}-ward-{int(cast(str, ward))}", code)
    d.contest(
        tag,
        election,
        kind,
        title,
        slug,
        magnitude=magnitude,
        method=hv.method(),
        english=english,
        office=office,
        body=body,
        district_area=place(code, name),
        system=system,
    )
    return tag


def service_day(d: Day, tree: Tree, service: str, vl_id: int, group: list[Event]) -> None:
    crawl_files = read_crawl(service, vl_id)
    if crawl_files is None:
        return
    d.cite(crawl_url(service, vl_id))
    for event in group:
        base = event.folder
        protocols = [n for n in crawl_files if n.startswith(f"{base}/szavossz/") and "/SzavkorJkv-" in n]
        totals = [n for n in crawl_files if n.startswith(f"{base}/szavossz/") and "/SzeredmTelep-" in n]
        marks = [n for n in crawl_files if n.startswith(f"{base}/ver/") and n.endswith("/Jelolesek.json")]
        if not totals or not marks:
            continue
        nominated = cast(dict[str, object], cast(dict[str, object], crawl_files[marks[0]])["data"])
        names = {int(cast(int, o["szkod"])): str(o["nev"]) for o in items(nominated.get("szervezetek"))}
        finals = cast(dict[str, object], cast(dict[str, object], crawl_files[totals[0]])["data"])
        code = event.maz + event.taz
        name = str(finals["tel_nev"])
        if service == "inemz":
            nationality_event(d, tree, event, finals, names, code, name)
            continue
        if not protocols:
            continue
        data = cast(dict[str, object], cast(dict[str, object], crawl_files[protocols[0]])["data"])
        ballots: dict[tuple[str, str | None], list[dict[str, object]]] = {}
        for ballot in items(data.get("szavazolapok")):
            kind = str(ballot["valaltip"])
            if ballot.get("tevk_csoport"):
                for ward in items(ballot["tevk_csoport"]):
                    ballots[(kind, str(ward["tevk"]))] = items(ward.get("jeloltek"))
            else:
                ballots[(kind, None)] = items(ballot.get("jeloltek"))
        options: dict[tuple[str, str | None, int], str] = {}
        tags: dict[tuple[str, str | None], str] = {}
        for (kind, ward), entries in ballots.items():
            role = LOCAL_KINDS.get(kind)
            if role is None:
                raise ValueError(f"{base}: unknown ballot kind {kind}")
            elected = sum(1 for entry in entries if entry.get("mandatum") == 1)
            tag = tags[(kind, ward)] = local_contest(d, tree, role, code, name, ward, elected)
            for entry in entries:
                position = int(cast(int, entry["szavlap_sorsz"]))
                nominator = nominators(entry, names)
                printed = str(entry["jelolt_neve"])
                ident = f"ej{entry['ej_id']}"
                option = d.option(tag, printed, "candidate", nominator=nominator, position=position, ident=ident)
                if d.first(tag, ident):
                    d.candidate(tag, option, f"ej:{entry['ej_id']}", printed, nominator=nominator)
                if entry.get("mandatum") == 1:
                    d.outcome(tag, option, "elected")
                options[(kind, ward, position)] = option
        protocol_rows = [(None, row) for row in items(data.get("jegyzokonyvek"))]
        protocol_rows += [
            (str(group_["tevk"]), row)
            for group_ in items(data.get("tevk_csoport"))
            for row in items(group_.get("jegyzokonyvek"))
        ]
        for ward, row in protocol_rows:
            kind = str(row["valaltip"])
            index = (kind, ward if kind == "4" else None)
            if index not in tags:
                continue
            tag = tags[index]
            unit = d.unit(code, str(row["sorsz"]))
            for item in items(row.get("tetelek")):
                d.value(
                    tag, unit, options[(*index, int(cast(int, item["szavlap_sorsz"])))], 1, number(item.get("szavazat"))
                )
            d.accounting(
                tag,
                unit,
                number(row.get("vp_osszes")),
                number(row.get("szk_megjelent")),
                number(row.get("szl_belyegzett_urna")),
                number(row.get("szl_ervenytelen")),
                number(row.get("szl_ervenyes")),
            )
        declared_rows = [(None, row) for row in items(finals.get("eredmenyek"))]
        declared_rows += [
            (str(group_["tevk"]), row)
            for group_ in items(finals.get("tevk_csoport"))
            for row in items(group_.get("eredmenyek"))
        ]
        for ward, row in declared_rows:
            kind = str(row["valaltip"])
            index = (kind, ward if kind == "4" else None)
            if index not in tags:
                continue
            tag = tags[index]
            for item in items(row.get("tetelek")):
                d.declare(
                    tag, options[(*index, int(cast(int, item["szavlap_sorsz"])))], 1, number(item.get("szavazat"))
                )
            for measure, field in (
                (2, "vp_osszes"),
                (14, "vp_megjelent"),
                (3, "szl_belyegzett_urna"),
                (5, "szl_ervenytelen"),
                (4, "szl_ervenyes"),
            ):
                d.declare(tag, None, measure, number(row.get(field)))


def nationality_event(
    d: Day,
    tree: Tree,
    event: Event,
    finals: dict[str, object],
    names: dict[int, str],
    code: str,
    name: str,
) -> None:
    election, _ = tree.child("nationality")
    for ballot in items(finals.get("szavazolapok")):
        nation = str(ballot["nemzetiseg"])
        nemzkod = str(ballot["nemzkod"])
        tag = f"ik:{d.day}:nationality:{code}:{nemzkod}"
        entries = items(ballot.get("jeloltek"))
        elected = sum(1 for entry in entries if entry.get("mandatum") == 1)
        d.contest(
            tag,
            election,
            "body_seats",
            f"{name} {nation.lower()} nemzetiségi önkormányzat",
            tree.slug("nationality", f"{name}-{nation}", code),
            magnitude=max(elected, 1),
            method=METHOD_NATIONALITY,
            english=f"{nation} self-government of {name}",
            body=council_body(code, name, nation),
            district_area=place(code, name),
            system=NATIONALITY_BLOCK,
        )
        options: dict[int, str] = {}
        for entry in entries:
            position = int(cast(int, entry["szavlap_sorsz"]))
            nominator = nominators(entry, names)
            printed = str(entry["jelolt_neve"])
            ident = f"ej{entry['ej_id']}"
            option = d.option(tag, printed, "candidate", nominator=nominator, position=position, ident=ident)
            d.candidate(tag, option, f"ej:{entry['ej_id']}", printed, nominator=nominator)
            if entry.get("mandatum") == 1:
                d.outcome(tag, option, "elected")
            options[position] = option
        unit = d.settlement_unit(code)
        for row in items(finals.get("eredmenyek")):
            if str(row.get("nemzkod")) != nemzkod:
                continue
            for item in items(row.get("tetelek")):
                d.value(tag, unit, options[int(cast(int, item["szavlap_sorsz"]))], 1, number(item.get("szavazat")))
            d.accounting(
                tag,
                unit,
                number(row.get("vp_osszes")),
                number(row.get("vp_megjelent")),
                number(row.get("szl_belyegzett_urna")),
                number(row.get("szl_ervenytelen")),
                number(row.get("szl_ervenyes")),
            )


# ---------------------------------------------------------------------------------------------------------------
# Archive workbooks


@functools.cache
def settlement_codes() -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """The office's settlement names by code, and the codes by county and folded name."""
    rows = json.loads(raw.cached(SOURCE, SETTLEMENTS).path.read_bytes())["list"]
    names: dict[str, str] = {}
    by_name: dict[tuple[str, str], str] = {}
    for row in rows:
        entry = cast(dict[str, object], row.get("leiro", row))
        if str(entry["maz"]) == "99":
            continue
        code = f"{entry['maz']}{entry['taz']}"
        names[code] = str(entry["megnev"])
        by_name.setdefault((str(entry["maz"]), hv.fold(str(entry["megnev"]))), code)
    return names, by_name


def settlement_code(card: Card) -> tuple[str, str]:
    """The office's code and name for the settlement of a card, falling back to a name-based code."""
    names, by_name = settlement_codes()
    maz = card.maz or hv.county_key(card.county)
    if card.maz and card.taz:
        code = f"{card.maz}{card.taz}"
        return code, card.place
    found = by_name.get((maz, hv.fold(card.place)))
    if found is not None:
        return found, names[found]
    return f"{maz}-{changeset.slug(card.place)}", card.place


def read_workbook(files: dict[str, bytes], card: Card) -> dict[str, list[list[str]]]:
    assert card.url is not None
    content = files[card.url.removeprefix(PORTAL + "/")]
    reader = fastexcel.read_excel(content)
    return {
        name: [
            ["" if c is None else str(c).strip() for c in row]
            for row in reader.load_sheet_by_name(name, header_row=None).to_polars().rows()
        ]
        for name in reader.sheet_names
    }


def archive_files(category: str, day: date) -> dict[str, bytes] | None:
    url = f"{PORTAL}/{category}/{day}#crawl"
    if url not in raw.keys(SOURCE):
        return None
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return files or None


def figure(text: str) -> int | None:
    return int(text.replace(" ", "")) if re.fullmatch(r"\d[\d ]*", text) else None


def archive_results(
    d: Day, tag: str, rows: list[list[str]], unit: str, status: str, *, magnitude_rule: bool = True
) -> None:
    """Stage the options, candidates, and votes of one contest from a workbook's candidate sheet."""
    header = rows[0]
    votes_column = next(i for i, h in enumerate(header) if h.startswith("Kapott"))
    nominator_column = next(i for i, h in enumerate(header) if h.startswith("Jelölő"))
    elected_column = next(i for i, h in enumerate(header) if h.startswith("Polg"))
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        position = int(row[0])
        name, nominator = row[1], row[nominator_column] or "Független"
        option = d.option(tag, name, "candidate", nominator=nominator, position=position, ident=f"n{position}")
        d.candidate(tag, option, f"{tag}:{position}", name, nominator=nominator)
        d.value(tag, unit, option, 1, figure(row[votes_column]))
        if row[elected_column].strip():
            d.outcome(tag, option, "elected")


def archive_day(d: Day, tree: Tree, category: str) -> None:
    files = archive_files(category, d.day)
    if files is None:
        return
    d.cite(f"{PORTAL}/{category}/{d.day}#crawl")
    d.cite(ARCHIVE_PAGES[category])
    d.reasons["accounting.votes_valid"] = (
        "the candidate votes in the office's workbook do not add up to the valid ballots it prints for the same "
        "settlement; stored as printed"
    )
    for card in cards(category):
        if card.day != d.day or card.url is None:
            continue
        book = read_workbook(files, card)
        if category == "parliament":
            parliament_card(d, tree, card, book)
            continue
        code, name = settlement_code(card)
        place(code, name)
        kind = card.kind.lower()
        facts = book["Névjegyzéki, szavazási adatok"][1]
        status = book["CÍMLAP"][0][0].splitlines()[-1]
        seats = re.search(r"megválasztandó képviselők száma: (\d+)", book["CÍMLAP"][0][0])
        sheet = book["Jelöltek eredmenyei"]
        if category == "nationality":
            if "települési" not in kind:
                continue
            nation = card.kind.split(" települési")[0].strip()
            nemzkod = changeset.slug(nation)
            tag = f"ik:{d.day}:nationality:{code}:{nemzkod}"
            elected = sum(1 for row in sheet[1:] if len(row) > 4 and row[-1].strip())
            election, _ = tree.child("nationality")
            d.contest(
                tag,
                election,
                "body_seats",
                f"{name} {nation.lower()} nemzetiségi önkormányzat",
                tree.slug("nationality", f"{name}-{nation}", code),
                magnitude=max(elected, int(seats[1]) if seats else 1, 1),
                method=METHOD_NATIONALITY,
                english=f"{nation} self-government of {name}",
                body=council_body(code, name, nation),
                district_area=place(code, name),
                system=NATIONALITY_BLOCK,
            )
            unit = d.settlement_unit(code)
        else:
            if "polgármester" in kind:
                role, ward = "mayor", None
            elif "egyéni választókerületi" in kind:
                found = re.search(r"\((\d+)\. EVK\)", card.kind)
                if found is None:
                    raise ValueError(f"{card}: no ward number")
                role, ward = "ward", found[1]
            else:
                role, ward = "council", None
            elected = sum(1 for row in sheet[1:] if row and row[-1].strip())
            tag = local_contest(d, tree, role, code, name, ward, int(seats[1]) if seats and int(seats[1]) else elected)
            unit = d.settlement_unit(code)
        d.accounting(
            tag,
            unit,
            figure(facts[0]),
            figure(facts[1]),
            figure(facts[3]),
            figure(facts[4]),
            figure(facts[5]),
        )
        archive_results(d, tag, sheet, unit, status)
        if "eredménytelen" in status:
            d.p.add(
                "note",
                id=hv.key("note", d.sets[tag], "invalid"),
                entity_id=d.sets[tag],
                kind="caveat",
                body_md="The office declared this election without a valid result (eredménytelen).",
                lang="en",
            )


def parliament_card(d: Day, tree: Tree, card: Card, book: dict[str, list[list[str]]]) -> None:
    maz = hv.county_key(card.place)
    found = re.match(r"(\d+)", card.county)
    if found is None:
        raise ValueError(f"{card}: no district number")
    number = int(found[1])
    code = f"2014:{maz}-{number:02d}"
    name = f"{hv.COUNTIES[maz]} {'főváros' if maz == '01' else 'megye'} {number:02d}. OEVK"
    STATE.districts[code] = (maz, f"{number:02d}", name)
    area = key("area", "oevk", code)
    election, _ = tree.child("parliament")
    tag = f"ik:{d.day}:parliament:{maz}{number:02d}"
    d.contest(
        tag,
        election,
        "body_seats",
        f"{hv.COUNTIES[maz]} {number}. OEVK",
        tree.slug("parliament", f"{hv.COUNTIES[maz]}-{number}", f"{maz}{number:02d}"),
        magnitude=1,
        method=METHOD_PARLIAMENT,
        english=f"{hv.COUNTIES[maz]} constituency {number}",
        body=PARLIAMENT,
        district_area=area,
        system=SMD_FPTP,
    )
    if "Munka1" in book:
        for row in book["Munka1"][2:]:
            if row and row[0]:
                place(row[4].split("-")[0] + row[4].split("-")[1], row[3])
        parliament_stations(d, tag, book["Munka1"])
        return
    unit = d.units[f"{d.day}:{code}"] = hv.key("unit", "ik", f"{d.day}:{code}")
    d.p.add(
        "reporting_unit",
        id=unit,
        polity_id=HUNGARY,
        scheme_code="hu.idokozi_oevk",
        code=f"{d.day}:{code}",
        area_relation="equals",
        area_id=area,
        kind="standard",
    )
    archive_results(d, tag, book["Jelöltek eredmenyei"][1:], unit, "")


def parliament_stations(d: Day, tag: str, rows: list[list[str]]) -> None:
    """The 2015 workbooks: one row per polling station, with a vote and a nominator column for each candidate."""
    header = rows[1]
    first = 18
    names = [header[i] for i in range(first, len(header), 2)]
    options: list[str] = []
    for position, name in enumerate(names, 1):
        row_nominator = next(r[first + 2 * (position - 1) + 1] for r in rows[2:] if r and r[0])
        option = d.option(tag, name, "candidate", nominator=row_nominator, position=position, ident=f"n{position}")
        d.candidate(tag, option, f"{tag}:{position}", name, nominator=row_nominator)
        options.append(option)
    for row in rows[2:]:
        if not row or not row[0]:
            continue
        station = row[4]
        maz, taz, number = station.split("-")
        unit = d.unit(maz + taz, number)
        for position, option in enumerate(options):
            d.value(tag, unit, option, 1, figure(row[first + 2 * position]))
        d.accounting(tag, unit, figure(row[5]), figure(row[10]), figure(row[14]), figure(row[16]), figure(row[17]))


METHOD_PARLIAMENT = (
    "Published by the National Election Office (Nemzeti Választási Iroda) for the whole single-member district: the "
    "valid votes each candidate received. The office's workbook for this election carries no turnout figures."
)


# ---------------------------------------------------------------------------------------------------------------
# Packages


def days() -> dict[date, list[tuple[str, int, list[Event]]]]:
    found: dict[date, list[tuple[str, int, list[Event]]]] = defaultdict(list)
    for service in SERVICES:
        by_id: dict[int, list[Event]] = defaultdict(list)
        for event in events(service):
            by_id[event.vl_id].append(event)
        for vl_id, group in sorted(by_id.items()):
            found[group[0].day].append((service, vl_id, group))
    for category in ARCHIVE_PAGES:
        for card in cards(category):
            found[card.day]
    return found


BUILDERS: list[object] = []


def build(day: date) -> Day:
    d = Day(day, f"Hungary's by-elections of {day} by polling station or settlement, from the National Election Office")
    tree = Tree(d)
    for service, vl_id, group in days()[day]:
        d.cite(list_url(service))
        service_day(d, tree, service, vl_id, group)
    for category in ARCHIVE_PAGES:
        archive_day(d, tree, category)
    return d


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The counties, settlements, and parliamentary districts of Hungary's by-elections, as the National Election "
        "Office names them",
    )
    p.external |= {HUNGARY, TERRITORY}
    for code, rank, kind, noun in (
        ("hu.idokozi_megye", 1, "administrative", "county"),
        ("hu.idokozi_oevk", 2, "electoral_district", "parliamentary constituency"),
        ("hu.idokozi_telepules", 3, "administrative", "settlement"),
        ("hu.idokozi_szavazokor", 4, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=HUNGARY, kind=kind, rank=rank, noun=noun, code_system=None)
    for maz in sorted(STATE.counties):
        id = key("area", "county", maz)
        p.add("area", id=id, scheme_code="hu.idokozi_megye")
        p.add("area_code", area_id=id, code=maz)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        name = hv.COUNTIES[maz]
        p.name(id, "Budapest" if maz == "01" else f"{name} vármegye", "hu", "official")
    for code, (maz, name) in sorted(STATE.places.items()):
        id = key("area", "settlement", code)
        p.add("area", id=id, scheme_code="hu.idokozi_telepules")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "county", maz))
        p.add("external_id", entity_id=id, scheme="hu.telepules", value=code)
        p.name(id, hv.person_text(name) if name.isupper() else name, "hu", "official")
    for code, (maz, number_, name) in sorted(STATE.districts.items()):
        id = key("area", "oevk", code)
        p.add("area", id=id, scheme_code="hu.idokozi_oevk")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "county", maz))
        p.name(id, name, "hu", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The sources, electoral systems, parties, bodies, and offices of Hungary's by-elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {HUNGARY}
    p.add(
        "source",
        id=NVI,
        kind="official_body",
        publisher="Nemzeti Választási Iroda",
        title="Időközi választások (valasztas.hu)",
        url=PAGE,
        license="Public information under Act CXII of 2011; reuse with the source acknowledged",
        license_url="https://www.valasztas.hu/impresszum",
        attribution="Nemzeti Választási Iroda, valasztas.hu",
    )
    stored: set[str] = set()
    for url in sorted(STATE.cited):
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key not in stored:
            stored.add(artifact.storage_key)
            p.artifact(artifact, NVI)
    p.add("body", id=PARLIAMENT, polity_id=HUNGARY, kind="unicameral")
    p.name(PARLIAMENT, "National Assembly", "en", "official")
    p.name(PARLIAMENT, "Országgyűlés", "hu", "native", False)
    for id, since, rules, text in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=HUNGARY, valid_from=since, valid_to=None, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for id, (english, native, kind) in sorted(STATE.bodies.items()):
        p.add("body", id=id, polity_id=HUNGARY, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, native, "hu", "native", False)
    for id, (english, native) in sorted(STATE.offices.items()):
        p.add("office", id=id, polity_id=HUNGARY, kind="mayor")
        p.name(id, english, "en", "official")
        p.name(id, native, "hu", "native", False)
    for folded, label in sorted(STATE.parties.items()):
        id = key("party", folded)
        p.add("party", id=id, kind="bloc" if re.search(r",|-KDNP|\bÉS\b", label) else "party", polity_id=HUNGARY)
        p.name(id, label, "hu", "official")
    return p


def packages() -> list[str]:
    from ea.sources.hu import idokozi_old

    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{day}" for day in sorted(built())),
        *idokozi_old.packages(),
    ]


@functools.cache
def built() -> dict[date, Day]:
    """The package of every date on which at least one by-election was held and its results are published."""
    found = {day: build(day) for day in sorted(days())}
    return {day: d for day, d in found.items() if d.sets}


def parse(only: str | None = None) -> None:
    for day, d in built().items():
        if only and only not in str(day):
            continue
        d.write()
    geo().write()
    registry().write()
    from ea.sources.hu import idokozi_old

    idokozi_old.parse(only)


def emit() -> None:
    package.run("load", packages())
