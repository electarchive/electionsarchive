"""Russia's municipal council and head elections of the single voting day of 14 September 2025, by precinct commission.

The Central Election Commission publishes the results on izbirkom.ru, whose hosts do not answer scripted requests
from outside Russia. The open-data release of that day, mirrored at gitlab.com/russian-electoral-open-data/edg-2025,
holds one CSV file per electoral district and campaign, with the candidates' table beside them. This module reads
the release at one pinned commit, kept in the raw cache as one archive of its results, candidates, and notes; the
source of every figure is still the Commission, and the mirror is where the file came from.

The release holds results for 2,262 municipal campaigns of the day (and 30 for the centers of federal subjects,
which are municipal councils too). Its federal subject and referendum campaigns are other classes and are left out.
A campaign's files are one per district; each file's rows are the total (`org`, or `oik` for a district's own
total), the remote electronic voting rows (`*_rev`), the territorial commissions (`tik`), and the precinct
commissions (`uik`). The precinct rows and the remote electronic voting rows hold the values, and the total is stored
as the declared total of the result set.

The releases for 2018 and 2024 hold only links to the Commission's pages, and the other years' repositories are
empty, so the other voting days are not built here.

The release names a municipal formation only inside the title of each election, in the genitive (`Морозовского
сельсовета Искитимского района Новосибирской области`). The area of a formation takes that phrase as its name,
because the release has no official code for it, and it has no outline.
"""

import csv
import gzip
import io
import re
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.ru import cik_heads as heads
from ea.sources.ru import cik_uik as base

SOURCE = "ru.cik_municipal"
RUSSIA = base.RUSSIA
MIRROR = "https://gitlab.com/russian-electoral-open-data/edg-2025"
COMMIT = "5838715420c2d8ed4825330546b599e27feb510a"
ARCHIVE = f"{MIRROR}/-/archive/{COMMIT}/edg-2025-{COMMIT}.tar.gz"
PATHS = ["results/csv", "candidates.tsv", "info.json", "README.md"]
DAY = date(2025, 9, 14)
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
SUBJECT = base.SUBJECT
TIK = base.TIK
UIK = base.UIK
MUNI = "ru.municipality"
PLAN_FROM = base.PLAN_FROM


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CIK = key("source", "cik")
HEAD_SYSTEM = key("electoral_system", "head")

# ----------------------------------------------------------------------------------------------------------------
# Raw files


def fetch() -> None:
    """Store the release's results and notes as one archive, built from a clone at the pinned commit."""
    if ARCHIVE in raw.keys(SOURCE):
        return
    with tempfile.TemporaryDirectory() as folder:
        clone = Path(folder) / "edg"
        git = ["git", "-C", str(clone)]
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        subprocess.run([*git, "fetch", "-q", "--depth", "1", f"{MIRROR}.git", COMMIT], check=True)
        packed = subprocess.run(
            [*git, "archive", "--format=tar", COMMIT, *PATHS], check=True, capture_output=True
        ).stdout
    raw.store(
        SOURCE,
        ARCHIVE,
        gzip.compress(packed, mtime=0),
        suffix=".tar.gz",
        retrieved_at=datetime.now(UTC),
        media_type="application/gzip",
    )


def norm(name: str) -> str:
    return " ".join(name.lower().replace("ё", "е").split())


def read_table(content: bytes) -> list[dict[str, str]]:
    csv.field_size_limit(2**30)
    table = list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))
    if not table:
        return []
    header = table[0]
    return [dict(zip(header, [*row, *[""] * (len(header) - len(row))], strict=False)) for row in table[1:]]


@dataclass
class Release:
    info: dict[str, dict[str, object]]
    people: dict[tuple[str, str], dict[str, str]]
    files: dict[str, list[tuple[str, list[dict[str, str]]]]]
    named: dict[str, set[str]]


def release() -> Release:
    import json

    members: dict[str, bytes] = {}
    with tarfile.open(raw.cached(SOURCE, ARCHIVE).path, "r:gz") as tar:
        for member in tar:
            if member.isfile():
                handle = tar.extractfile(member)
                if handle is not None:
                    members[member.name] = handle.read()
    info: dict[str, dict[str, object]] = {}
    for level, campaigns in json.loads(members["info.json"])["campaigns"].items():
        for campaign in campaigns:
            campaign["_level"] = level
            info[str(campaign["id"])] = campaign
    people: dict[tuple[str, str], dict[str, str]] = {}
    for row in read_table(members["candidates.tsv"]):
        if row["fullName"]:
            people.setdefault((norm(row["name"]), norm(row["fullName"])), row)
    named: dict[str, set[str]] = {}
    for title, name in people:
        named.setdefault(title, set()).add(name)
    files: dict[str, list[tuple[str, list[dict[str, str]]]]] = {}
    for name in sorted(members):
        if not name.startswith("results/csv/"):
            continue
        rows = read_table(members[name])
        if not rows:
            continue
        found = re.search(r"election/(\d+)/", rows[0]["url"])
        if found is None:
            raise LookupError(f"{name} has no election id in its links")
        files.setdefault(found.group(1), []).append((name, rows))
    return Release(info, people, files, named)


def wanted(campaign: dict[str, object]) -> bool:
    """Municipal and county-seat campaigns for deputies or heads; subject campaigns and referendums are other classes."""
    kind = campaign["kind"]
    assert isinstance(kind, dict)
    return campaign["_level"] in ("municipal", "adm_center") and kind["value"] in (
        "Выборы депутатов",
        "Выборы на должность",
    )


# ----------------------------------------------------------------------------------------------------------------
# Names

PREFIX = re.compile(r"^(?:(?:дополнительные|досрочные|повторные|отложенное)\s+)?(?:выборы|довыборы)\s+", re.IGNORECASE)
KIND = re.compile(r"^(?:на должность\s+)?(?:депутатов|депутата|главы)\s+", re.IGNORECASE)
TERM = re.compile(r"\s+(?:[IVXLC]+|[а-яё-]+)\s+созыва\s*$", re.IGNORECASE)
WARD = re.compile(r"\s+по\s+(?:\S+\s+){0,3}(?:округу|округам).*$", re.IGNORECASE)
BODIES = re.compile(
    r"^(?:представительного органа|совета депутатов|собрания депутатов|совета|думы|собрания|совет депутатов)\s+",
    re.IGNORECASE,
)


def is_head(title: str) -> bool:
    return bool(
        re.match(
            r"^(?:(?:дополнительные|досрочные|повторные|отложенное)\s+)?(?:выборы|довыборы)\s+главы",
            " ".join(title.split()),
            re.IGNORECASE,
        )
    )


def formation(title: str) -> str:
    """The formation's phrase in the election's title: without the kind of vote, the body's name, and the convocation."""
    text = " ".join(title.split())
    text = PREFIX.sub("", text)
    text = KIND.sub("", text)
    text = TERM.sub("", text)
    text = WARD.sub("", text)
    text = BODIES.sub("", text)
    return text.strip()


def body_name(title: str) -> str:
    text = " ".join(title.split())
    text = PREFIX.sub("", text)
    text = KIND.sub("", text)
    text = TERM.sub("", text)
    return WARD.sub("", text).strip()


def head_name(title: str) -> str:
    return "Глава " + formation(title)


# ----------------------------------------------------------------------------------------------------------------
# Parties

PARTIES = heads.PARTIES + ((r"ЯБЛОКО", "Российская объединённая демократическая партия «Яблоко»"),)
GLOBAL = {
    "er": "Единая Россия",
    "kprf": "Коммунистическая партия Российской Федерации",
    "ldpr": "Либерально-демократическая партия России",
    "sr": "Справедливая Россия — За правду",
    "nl": "Новые люди",
    "rodina": "Родина",
    "yabloko": "Российская объединённая демократическая партия «Яблоко»",
}
SPECIAL = {"за", "против"}
LISTWORDS = re.compile(r"партия|партии|отделение|«|\"", re.IGNORECASE)


def party_of(text: str, code: str = "") -> str | None:
    if text.strip().lower() == heads.SELF:
        return None
    for pattern, name in PARTIES:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return GLOBAL.get(code)


def party_id(name: str) -> str:
    return key("party", name)


# ----------------------------------------------------------------------------------------------------------------
# Packages


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2025-09-14"]


def parse() -> None:
    data = release()
    wards = formations(data)
    geo(wards).write()
    registry(wards).write()
    day(data, wards).write()


def emit() -> None:
    package.run("load", packages())


@dataclass(frozen=True)
class Formation:
    iso: str
    area: str
    phrase: str
    title: str
    head: bool


def subject_of(campaign: dict[str, object]) -> tuple[str, heads.Region]:
    subject = campaign["subjectRf"]
    assert isinstance(subject, dict)
    name = str(subject["value"])
    if name == "Республика Крым":
        return name, heads.Region("UA-43", base.CRIMEA, False)
    return name, heads.region(name)


def formations(data: Release) -> dict[str, Formation]:
    """Each wanted campaign with results, by the Commission's id, with the municipal formation it elects for."""
    found: dict[str, Formation] = {}
    for eid in sorted(data.files):
        campaign = data.info[eid]
        if not wanted(campaign):
            continue
        _, where = subject_of(campaign)
        phrase = formation(str(campaign["name"]))
        iso = where.code
        title = " ".join(str(campaign["name"]).split())
        found[eid] = Formation(iso, key("area", iso, norm(phrase)), phrase, title, is_head(title))
    return found


def _source(p: Package) -> None:
    p.add(
        "source",
        id=CIK,
        kind="official_body",
        publisher="Центральная избирательная комиссия Российской Федерации",
        title="Результаты муниципальных выборов по участковым избирательным комиссиям (izbirkom.ru)",
        url="http://www.izbirkom.ru/region/izbirkom",
        license="Official election results of a state body; not subject to copyright (Civil Code of the Russian "
        "Federation, art. 1259(6))",
        license_url="https://www.consultant.ru/document/cons_doc_LAW_64629/",
        attribution="Центральная избирательная комиссия Российской Федерации",
        notes=f"The Commission's tables as the open-data release of the single voting day of 14 September 2025 "
        f"keeps them, read from {MIRROR} at commit {COMMIT}; izbirkom.ru does not answer scripted requests from "
        "outside Russia.",
    )


def geo(wards: dict[str, Formation]) -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The municipal formations that held council or head elections on 14 September 2025, under their federal "
        "subjects",
        depends=["ru.cik_uik/geo"],
    )
    p.external |= {RUSSIA, heads.SEVASTOPOL, base.CRIMEA}
    p.add(
        "scheme",
        code=MUNI,
        polity_id=RUSSIA,
        kind="administrative",
        rank=2,
        noun="municipal formation",
        code_system=None,
    )
    seen: set[str] = set()
    for ward in wards.values():
        if ward.area in seen:
            continue
        seen.add(ward.area)
        p.add("area", id=ward.area, scheme_code=MUNI)
        p.add("area_parent", area_id=ward.area, parent_id=_subject_area(ward.iso), valid_from=DAY, valid_to=None)
        p.name(ward.area, ward.phrase, "ru", "official")
    return p


def _subject_area(code: str) -> str:
    if code == "UA-43":
        return base.CRIMEA
    return heads.SEVASTOPOL if code == "UA-40" else heads.subject_area(code.removeprefix("RU-"))


def registry(wards: dict[str, Formation]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The councils and heads of the municipal formations that held elections on 14 September 2025, their "
        "electoral system, and the parties that nominated candidates, from the Central Election Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(RUSSIA)
    p.add(
        "electoral_system",
        id=HEAD_SYSTEM,
        polity_id=RUSSIA,
        valid_from=date(2003, 10, 6),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", HEAD_SYSTEM),
        entity_id=HEAD_SYSTEM,
        kind="methodology",
        body_md="A municipal head elected directly is chosen by the most votes in a single round (Law No. 131-FZ, "
        "art. 36 and 38, and the formation's charter).",
        lang="en",
    )
    seen: set[str] = set()
    for _, name in PARTIES:
        if name in seen:
            continue
        seen.add(name)
        p.add("party", id=party_id(name), kind="party", polity_id=RUSSIA)
        p.name(party_id(name), name, "ru", "official")
    made: set[str] = set()
    for ward in wards.values():
        id = office_id(ward.area) if ward.head else body_id(ward.area)
        if id in made:
            continue
        made.add(id)
        if ward.head:
            p.add("office", id=id, polity_id=RUSSIA, kind="mayor")
            p.name(id, head_name(ward.title), "ru", "official")
        else:
            p.add("body", id=id, polity_id=RUSSIA, kind="council")
            p.name(id, body_name(ward.title), "ru", "official")
    return p


def body_id(area: str) -> str:
    return key("body", area)


def office_id(area: str) -> str:
    return key("office", area)


# ----------------------------------------------------------------------------------------------------------------
# Units


def whole(text: str | None) -> int | None:
    try:
        return int((text or "").strip())
    except ValueError:
        return None


def _sum(row: dict[str, str], headers: list[str]) -> int | None:
    found = [v for v in (whole(row[h]) for h in headers) if v is not None]
    return sum(found) if found else None


SUBCOLUMNS = (
    "число",
    "в том числе",
    "в помещени",
    "- в",
    "-в",
    "проголосовавшим",
    "-проголосовавшим",
    "в труднодоступных",
    "общее число",
    "обнаружено",
    "досрочно",
    "в тик",
    "в окружной",
)


def is_count(header: str) -> bool:
    """A column of the accounting, or a part of one, rather than a ballot line."""
    return " ".join(header.lower().split()).startswith(SUBCOLUMNS)


def classify(header: str) -> str | None:
    text = " ".join(header.lower().split())
    if text.startswith("общее число недействительных"):
        return "invalid"
    return heads.classify(header)


METHOD = (
    "The Central Election Commission's results by precinct election commission (УИК) for the election of 14 "
    "September 2025, as the open-data release of that day keeps them. Registered are the voters on the lists at the "
    "close of voting, ballots cast are the ballots found in the portable and stationary boxes (for remote electronic "
    "voting, the valid and invalid ballots), and invalid and valid are the ballots so counted. Remote electronic "
    "voting is its own unit under its commission. The release's total for the district or the formation is the "
    "declared total."
)


class Day:
    """The units, parties, and people of the day's package, each kept once."""

    def __init__(self, p: Package) -> None:
        self.p = p
        self.units: set[str] = set()
        self.persons: set[str] = set()
        self.values: list[tuple[str, str, str | None, int, int]] = []

    def unit(
        self, eid: str, ward: Formation, scheme: str, label: str, parent: str | None, kind: str = "standard"
    ) -> str:
        id = key("unit", scheme, f"{DAY}:{eid}:{label}")
        if id not in self.units:
            self.units.add(id)
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=RUSSIA,
                scheme_code=scheme,
                code=f"{DAY}:{eid}:{label}",
                parent_id=parent,
                area_relation="equals" if scheme == MUNI else "within",
                area_id=ward.area,
                kind=kind,
            )
        return id


def day(data: Release, wards: dict[str, Formation]) -> Package:
    p = Package(
        f"{SOURCE}/2025-09-14",
        SOURCE,
        "The municipal council and head elections of 14 September 2025, by precinct election commission, from the "
        "Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", "ru.cik_uik/geo"],
    )
    p.external |= {RUSSIA, heads.SEVASTOPOL, base.CRIMEA}
    _source(p)
    p.artifact(raw.cached(SOURCE, ARCHIVE), CIK)
    path = "ru/2025-09-14-municipal"
    election = key("election", DAY)
    p.add("election", id=election, polity_id=RUSSIA, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "2025 Russian municipal elections", "en", "official")
    p.name(election, "Муниципальные выборы единого дня голосования 2025 года", "ru", "native", False)
    state = Day(p)
    gaps: list[str] = []
    missing: list[str] = []
    frames: list[pl.DataFrame] = []
    for eid, ward in wards.items():
        title, head = ward.title, ward.head
        for index, (_, rows) in enumerate(data.files[eid], 1):
            _contest(p, state, data, election, path, eid, ward, head, title, index, rows, gaps, missing, frames)
    if frames:
        p.frame("result_value", pl.concat(frames))
    if gaps:
        shown = "; ".join(gaps[:8])
        p.accept(
            "accounting.declared",
            f"In {len(gaps)} result sets the release's precinct rows do not add up to the total it prints for the "
            f"district or formation, because it leaves out precincts or prints a total that differs from them; the "
            f"first are {shown}",
        )
        p.accept(
            "accounting.ballots_sum",
            "In some precincts, mostly of multi-member districts, the release prints no valid ballots (0) or a "
            "figure far below the votes cast, so the ballots counted in its boxes exceed its valid plus invalid "
            "ballots; the figures are as the release prints them.",
        )
        p.accept(
            "accounting.votes_valid",
            "In a few precincts, of single-member contests, the candidates' votes the release prints differ from "
            "its valid ballots, usually where it prints 0 valid ballots against votes cast; the figures are as the "
            "release prints them.",
        )
    return p


def _contest(
    p: Package,
    state: Day,
    data: Release,
    election: str,
    path: str,
    eid: str,
    ward: Formation,
    head: bool,
    title: str,
    index: int,
    rows: list[dict[str, str]],
    gaps: list[str],
    missing: list[str],
    frames: list[pl.DataFrame],
) -> None:
    headers = list(rows[0])
    columns: dict[str, list[str]] = {}
    for header in headers[9:]:
        kind = classify(header) if is_count(header) else None
        if kind and kind not in columns:
            columns[kind] = [header]
    names = [h for h in headers[9:] if not is_count(h)]
    known = data.named.get(norm(title), set())
    named = [h for h in names if norm(h) not in SPECIAL and not norm(h).startswith("против всех")]
    lists = (
        bool(named)
        and not any(norm(h) in known for h in named)
        and any(party_of(h) or LISTWORDS.search(h) for h in named)
    )
    district = next((r["oik"] for r in rows if r["line_type"] == "oik" and r["oik"]), "")
    slug = f"{path}/{eid}" + (f"-{index}" if index > 1 else "")
    tag = f"{eid}:{index}"
    contest = key("contest", DAY, tag)
    candidates = [h for h in named if not lists]
    elected_names = {
        h for h in candidates if (row := data.people.get((norm(title), norm(h)))) is not None and row["election"] != ""
    }
    if head:
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=office_id(ward.area),
            district_area_id=ward.area,
            magnitude=1,
            electoral_system_id=HEAD_SYSTEM,
            default_result_kind="counted",
            term="full",
        )
    else:
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation" if lists else "body_seats",
            body_id=body_id(ward.area),
            district_area_id=ward.area,
            magnitude=None if lists else max(len(elected_names), seats(rows, names, columns), 1),
            default_result_kind="counted",
        )
    p.name(contest, title + (f" — {district}" if district else ""), "ru", "official")
    p.add("slug", path=slug, entity_id=contest, is_current=True)
    result_set = key("result_set", DAY, tag)
    declared_rows = [r for r in rows if r["line_type"] in ("org", "iks")] or (
        [r for r in rows if r["line_type"] == "oik"] if sum(1 for r in rows if r["line_type"] == "oik") == 1 else []
    )
    held_rows = [r for r in rows if r["line_type"] == "uik" or r["line_type"].endswith("_rev")]
    if not any(r["line_type"] == "uik" for r in rows):
        held_rows = [r for r in rows if r["line_type"] in ("tik", "tik_rev", "oik_rev", "org_rev")]
    declared = sum(_sum(r, columns.get("valid", [])) or 0 for r in declared_rows[:1])
    held = sum(_sum(r, columns.get("valid", [])) or 0 for r in held_rows)
    completeness = round(held / declared, 6) if declared and held <= declared else (1 if declared else None)
    if declared and held != declared:
        gaps.append(f"{title} ({district or 'all'}): {held:,} of {declared:,} valid ballots")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CIK,
        status="active",
        is_default=True,
        completeness=completeness,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    for position, printed in enumerate(names, 1):
        option = key("option", DAY, tag, printed)
        options[printed] = option
        ballot = " ".join(printed.split())
        special = (
            "answer"
            if norm(printed) in SPECIAL
            else "none_of_the_above"
            if norm(printed).startswith("против всех")
            else None
        )
        if special:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind=special,
                party_id=None,
                ballot_position=position,
                ballot_label=ballot,
            )
            continue
        if lists:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=_party(p, printed),
                ballot_position=position,
                ballot_label=ballot,
            )
            continue
        row = data.people.get((norm(title), norm(printed)))
        party = party_of(row["electionAssociation"], row["globalPartyName"]) if row else None
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party_id(party) if party else None,
            ballot_position=position,
            ballot_label=ballot,
        )
        born = row["birthDate"] if row and row["birthDate"] else None
        person = key("person", DAY, eid, ballot, born or "")
        if person not in state.persons:
            state.persons.add(person)
            p.add(
                "person",
                id=person,
                birth_date=date.fromisoformat(born) if born else None,
                birth_precision="day" if born else None,
            )
            p.name(person, ballot, "ru", "official")
        p.add(
            "candidacy",
            id=key("candidacy", DAY, tag, ballot),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=ballot,
        )
        if row is None:
            missing.append(f"{title}: {ballot}")
        else:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if row["election"] != "" else "lost",
            )
    top = state.unit(eid, ward, MUNI, "all", None)
    tiks: dict[str, str] = {}

    def parent(row: dict[str, str]) -> str:
        if row["tik"]:
            if row["tik"] not in tiks:
                tiks[row["tik"]] = state.unit(eid, ward, TIK, f"tik:{' '.join(row['tik'].split())}", top)
            return tiks[row["tik"]]
        return top

    out: list[tuple[str, str, str | None, int, int]] = []

    def put(unit: str, row: dict[str, str], remote: bool) -> None:
        valid = _sum(row, columns.get("valid", []))
        invalid = _sum(row, columns.get("invalid", []))
        registered = _sum(row, columns.get("registered", []))
        cast = _sum(row, columns.get("portable", []) + columns.get("stationary", []))
        if cast is None or remote:
            cast = (valid or 0) + (invalid or 0) if valid is not None or invalid is not None else None
        for measure, value in ((REGISTERED, registered), (VALID, valid), (INVALID, invalid), (BALLOTS, cast)):
            if value is not None:
                out.append((result_set, unit, None, measure, value))
        for printed, option in options.items():
            out.append((result_set, unit, option, VOTES, whole(row[printed]) or 0))

    for row in declared_rows[:1]:
        for measure, label in ((REGISTERED, "registered"), (VALID, "valid"), (INVALID, "invalid")):
            total = _sum(row, columns.get(label, []))
            if total is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total)
        for printed, option in options.items():
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=whole(row[printed]) or 0)
    for row in held_rows:
        kind = row["line_type"]
        if kind.endswith("_rev"):
            where = parent(row) if kind == "tik_rev" else top
            label = (
                f"rev:{' '.join(row['tik'].split())}" if kind == "tik_rev" else f"rev:{' '.join(row['oik'].split())}"
            )
            put(state.unit(eid, ward, UIK, label, where, "aggregate"), row, True)
        elif kind == "uik":
            number = row["uik_number"] or " ".join(row["uik"].split())
            put(
                state.unit(eid, ward, UIK, f"uik:{row['tik'] and ' '.join(row['tik'].split())}:{number}", parent(row)),
                row,
                False,
            )
        else:
            put(parent(row) if kind == "tik" else top, row, False)
    frames.append(
        pl.DataFrame(
            {
                "result_set_id": [v[0] for v in out],
                "unit_id": [v[1] for v in out],
                "option_id": [v[2] for v in out],
                "channel": [0] * len(out),
                "measure": [v[3] for v in out],
                "count_round": [0] * len(out),
                "flags": [0] * len(out),
                "value": [v[4] for v in out],
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int64,
                "measure": pl.Int64,
                "count_round": pl.Int64,
                "flags": pl.Int64,
                "value": pl.Int64,
            },
        )
    )


def seats(rows: list[dict[str, str]], names: list[str], columns: dict[str, list[str]]) -> int:
    """The most votes any precinct's voters cast on one ballot: a district elects at least that many members."""
    if any(norm(h) in SPECIAL for h in names):
        return 1
    ratio = 1
    for row in rows:
        if row["line_type"] != "uik":
            continue
        valid = _sum(row, columns.get("valid", []))
        if valid:
            votes = sum(whole(row[h]) or 0 for h in names)
            ratio = max(ratio, -(-votes // valid))
    return ratio


def _party(p: Package, printed: str) -> str | None:
    name = party_of(printed)
    return party_id(name) if name else None
