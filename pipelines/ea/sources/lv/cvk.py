import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "lv.cvk"
LATVIA = str(uuid5(package.NAMESPACE, "polity\x1fLV"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fLVA"))
REGISTERED, CAST, VALID, VOTES = 2, 3, 4, 1
PAGE = "https://data.gov.lv/dati/lv/organization/cvk"

URLS = {
    "epv2024/allresults-20240614105815_fixed_all_departments.json": "https://data.gov.lv/dati/dataset/da829174-dea8-4c5e-8b17-91dc84df14ba/resource/03809e72-bfc4-407b-a1aa-913e367564cb/download/allresults-20240614105815_fixed_all_departments.json",
    "epv2024/elected-20240614105815.json": "https://data.gov.lv/dati/dataset/da829174-dea8-4c5e-8b17-91dc84df14ba/resource/daf2d2c6-7671-40fb-8147-73bba030bc53/download/elected-20240614105815.json",
    "epv2024/lists-20240409094452.json": "https://data.gov.lv/dati/dataset/da829174-dea8-4c5e-8b17-91dc84df14ba/resource/44872b5b-f93c-4f05-b861-f1b44c9d8861/download/lists-20240409094452.json",
    "sv2022/candidatelists.json": "https://data.gov.lv/dati/dataset/b89db791-7f89-4969-87ac-0700b2f541a0/resource/186c246d-3adc-4c88-86a3-2eabf979f41a/download/candidatelists.json",
    "sv2022/departmentresults.json": "https://data.gov.lv/dati/dataset/b89db791-7f89-4969-87ac-0700b2f541a0/resource/76858736-6f50-4fdb-a495-d93b5c11dac9/download/departmentresults.json",
    "sv2022/departments.json": "https://data.gov.lv/dati/dataset/b89db791-7f89-4969-87ac-0700b2f541a0/resource/026eb30b-62b9-41de-8ba8-bfe8a2e572b6/download/departments.json",
    "sv2022/electedcandidates.json": "https://data.gov.lv/dati/dataset/b89db791-7f89-4969-87ac-0700b2f541a0/resource/0cdfc497-baa6-425d-adb1-104e77cf9188/download/electedcandidates.json",
    "ep2019/candidatelists.json": "https://data.gov.lv/dati/dataset/c5cc30b1-0dce-403b-a0b2-370defe4de6d/resource/f416c669-c702-4f8c-95c7-2811301146a7/download/candidatelists.json",
    "ep2019/elected.json": "https://data.gov.lv/dati/dataset/c5cc30b1-0dce-403b-a0b2-370defe4de6d/resource/02a08965-01d3-4c83-b1b5-a1e736f91cca/download/elected.json",
    "ep2019/locations.json": "https://data.gov.lv/dati/dataset/c5cc30b1-0dce-403b-a0b2-370defe4de6d/resource/e9bd1c65-8cb7-4216-8e82-c5db8eca88e4/download/locations.json",
    "ep2019/results.json": "https://data.gov.lv/dati/dataset/c5cc30b1-0dce-403b-a0b2-370defe4de6d/resource/43336221-17bd-4f67-a6f4-cb02e688df66/download/results.json",
    "sv2018/candidatelists.json": "https://data.gov.lv/dati/dataset/cb8d1838-4edc-465c-865c-0f6574f74029/resource/f3928060-801f-4a68-9f7a-c97ba49e4d9a/download/candidatelists.json",
    "sv2018/electedcandidates.json": "https://data.gov.lv/dati/dataset/cb8d1838-4edc-465c-865c-0f6574f74029/resource/5c7e6b4a-b732-468d-8237-5461a8ef9bf8/download/electedcandidates.json",
    "sv2018/locations.json": "https://data.gov.lv/dati/dataset/cb8d1838-4edc-465c-865c-0f6574f74029/resource/dd5ed1f3-ca3a-481b-a2dd-a5878d5fbb9d/download/locations.json",
    "sv2018/results.json": "https://data.gov.lv/dati/dataset/cb8d1838-4edc-465c-865c-0f6574f74029/resource/9057970d-5248-4eb6-9063-e2021a4d350b/download/results.json",
    "tn2011/results.xml": "https://data.gov.lv/dati/dataset/fa3806d2-d62d-4634-955e-c6ed65aecb67/resource/8a91bfb4-6db9-4ef1-be70-7d5665592699/download/results.xml",
    "tn2012/results_tn2012.xml": "https://data.gov.lv/dati/dataset/f11a2787-e98c-4e04-88b2-1e3c4606c162/resource/46cd1b8d-4281-4e33-a7a0-6a8e069accb4/download/results_tn2012.xml",
    "pv2017/candidatelists636312415130070000.xml": "https://data.gov.lv/dati/dataset/a7c824f2-cd4d-4556-a6ba-fcfe2b8bbd94/resource/8d8366c8-4984-4720-9ad6-b4bc4604d19d/download/candidatelists636312415130070000.xml",
    "pv2017/electedcandidates636363813214220000.xml": "https://data.gov.lv/dati/dataset/a7c824f2-cd4d-4556-a6ba-fcfe2b8bbd94/resource/a4f19194-c17b-4fea-ab21-f718afa707c6/download/electedcandidates636363813214220000.xml",
    "pv2017/electionresults636363813214220000.xml": "https://data.gov.lv/dati/dataset/a7c824f2-cd4d-4556-a6ba-fcfe2b8bbd94/resource/e6dd9296-ee2f-4892-9082-6da4c5feef26/download/electionresults636363813214220000.xml",
    "sv2014/rezultsxml": "https://data.gov.lv/dati/dataset/883279af-9d35-496d-bc30-f4a4fde984b0/resource/cbe31fc7-58e2-4af8-9edc-46951a5e1a9b/download/rezultsxml",
}

DAYS = {
    "2014-10-04": ("sv", "sv2014"),
    "2018-10-06": ("sv", "sv2018"),
    "2022-10-01": ("sv", "sv2022"),
    "2019-05-25": ("ep", "ep2019"),
    "2024-06-08": ("ep", "epv2024"),
}
SEATS = {"2019-05-25": 8, "2024-06-08": 9}
REFERENDUMS = {"2011-07-23": "tn2011", "2012-02-18": "tn2012"}
REGION_NAMES = {
    "1": ("Rīga", "Rīgas apgabals", "Riga constituency"),
    "2": ("Vidzeme", "Vidzemes apgabals", "Vidzeme constituency"),
    "3": ("Latgale", "Latgales apgabals", "Latgale constituency"),
    "4": ("Kurzeme", "Kurzemes apgabals", "Kurzeme constituency"),
    "5": ("Zemgale", "Zemgales apgabals", "Zemgale constituency"),
}
REGION_SLUGS = {"1": "riga", "2": "vidzeme", "3": "latgale", "4": "kurzeme", "5": "zemgale"}
METHOD = (
    "Counted per polling station (vēlēšanu iecirknis) and published by the Central Election Commission of Latvia "
    "(Centrālā vēlēšanu komisija) as open data. Each list's votes are its valid marks. Ballots cast are the voters "
    "who took part, and valid votes are the valid marks for lists. Ballots cast, valid votes, and registered voters "
    "are stored only where the commission's file prints them for the polling station. Polling stations abroad are "
    "counted with the Riga constituency in Saeima elections. The commission's files also hold each candidate's plus "
    "marks, minus marks, and valid-envelope counts; they are not stored here yet."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


CVK_SOURCE = key("source", "cvk")
SAEIMA = key("body", "saeima")


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def party_key(label: str) -> str:
    text = re.sub(r"[„”“\"'«»]", "", clean(label)).casefold()
    return re.sub(r"\s+", " ", text).strip()


def party(label: str) -> str:
    return key("party", party_key(label))


@dataclass
class Node:
    id: str
    parent: str | None
    type: int
    code: str
    name: str
    address: str
    voters: int | None
    cast: int | None
    valid: int | None
    seats: int | None = None
    lists: dict[int, int] = field(default_factory=dict)


@dataclass
class Elected:
    id: str
    name: str
    surname: str
    position: int
    list: int
    region: str | None


@dataclass
class Reading:
    nodes: dict[str, Node]
    names: dict[int, str]
    running: set[tuple[str | None, int]] | None
    elected: list[Elected]
    national: dict[int, int] = field(default_factory=dict)

    def region(self, node: Node) -> Node | None:
        while node.type > 1 and node.parent is not None:
            node = self.nodes[node.parent]
        return node if node.type == 1 else None


def path(name: str) -> raw.Artifact:
    return raw.cached(SOURCE, URLS[name])


def loaded(name: str) -> object:
    return json.loads(path(name).path.read_text(encoding="utf-8-sig"))


def _count(value: object) -> int | None:
    return value["Count"] if isinstance(value, dict) else None


def _results(prefix: str) -> tuple[dict[str, Node], dict[int, str]]:
    data = loaded(f"{prefix}/results.json" if prefix != "sv2022" else "sv2022/departmentresults.json")
    items = data["Results"] if isinstance(data, dict) else data
    nodes: dict[str, Node] = {}
    names: dict[int, str] = {}
    for item in items:
        loc = item.get("Location") or item["Department"]
        lists = {}
        for entry in item["CandidateLists"]:
            lists[int(entry["Number"])] = int(_count(entry["ValidMarkCount"]) or 0)
            names[int(entry["Number"])] = clean(entry["Name"])
        seats = loc.get("DeputyCount") or loc.get("DeputyLimit")
        voters = loc.get("VoterCount")
        nodes[loc["Id"]] = Node(
            id=loc["Id"],
            parent=loc.get("ParentId"),
            type=int(loc["Type"]),
            code=str(loc["Code"]),
            name=clean(loc["Name"]),
            address=clean(loc.get("Address")),
            voters=int(voters) if voters else None,
            cast=_count(item["VotedVoterCount"]),
            valid=_count(item["ValidMarkCount"]),
            seats=int(seats) if seats else None,
            lists=lists,
        )
    return nodes, names


def _running(prefix: str) -> set[tuple[str | None, int]] | None:
    if prefix == "sv2022":
        found: set[tuple[str | None, int]] = set()
        for entry in loaded("sv2022/candidatelists.json"):
            for where in entry["CandidateCountInRegions"]:
                if where["CandidateCount"]:
                    found.add((where["RegionId"], int(entry["Number"])))
        return found
    if prefix == "sv2018":
        return {
            (region, int(entry["Number"]))
            for entry in loaded("sv2018/candidatelists.json")["CandidateLists"]
            for region in entry["Regions"]
        }
    return None


def _elected(prefix: str) -> list[Elected]:
    found: list[Elected] = []
    if prefix == "sv2022":
        for e in loaded("sv2022/electedcandidates.json"):
            found.append(
                Elected(
                    e["Id"],
                    clean(e["Name"]),
                    clean(e["Surname"]),
                    int(e["Number"]),
                    int(e["CandidateList"]["Number"]),
                    e["Region"]["Id"],
                )
            )
    elif prefix == "sv2018":
        for e in loaded("sv2018/electedcandidates.json")["Candidates"]:
            found.append(
                Elected(
                    e["Id"],
                    clean(e["Name"]),
                    clean(e["Surname"]),
                    int(e["Number"]),
                    int(e["CandidateList"]["Number"]),
                    e["Region"]["Id"],
                )
            )
    elif prefix == "ep2019":
        for e in loaded("ep2019/elected.json"):
            found.append(
                Elected(
                    e["Id"],
                    clean(e["Name"]),
                    clean(e["Surname"]),
                    int(e["Number"]),
                    int(e["CandidateList"]["Number"]),
                    None,
                )
            )
    else:
        for e in loaded("epv2024/elected-20240614105815.json")["electedCandidates"]:
            found.append(
                Elected(
                    e["candAlias"],
                    clean(e["candName"]),
                    clean(e["candSurname"]),
                    int(e["candNpk"]),
                    int(e["partyNpk"]),
                    None,
                )
            )
    return found


def _tree() -> tuple[dict[str, Node], dict[int, str]]:
    data = loaded("epv2024/allresults-20240614105815_fixed_all_departments.json")
    nodes: dict[str, Node] = {}
    names: dict[int, str] = {}

    def walk(item: dict[str, object], parent: str | None) -> None:
        stats = item["departmentStats"]
        id = str(item["alias"])
        lists = {}
        for entry in stats["partiesStats"]:
            lists[int(entry["npk"])] = int(entry["paperGood"])
            names[int(entry["npk"])] = clean(entry["name"])
        nodes[id] = Node(
            id=id,
            parent=parent,
            type=int(item["DepartmentType"]),
            code=str(item["DepartmentNumber"]),
            name=clean(item["Name"]),
            address=clean(item.get("Address")),
            voters=stats["votersCanVote"],
            cast=stats["voted"],
            valid=stats["paperGood"],
            lists=lists,
        )
        for child in item.get("childs", []):
            walk(child, id)

    walk(data, None)
    return nodes, names


def _xml2014() -> Reading:
    root = ET.parse(path("sv2014/rezultsxml").path).getroot()
    base = root.find("PamatTabulas")
    nodes: dict[str, Node] = {}
    names: dict[int, str] = {}
    ballot: dict[str, int] = {}
    for entry in base.find("Saraksti"):
        ballot[entry.get("SarID")] = int(entry.get("Loze"))
        names[int(entry.get("Loze"))] = clean(entry.get("Nosaukums"))
    total = root.find("Valsts")
    nodes["0"] = Node(
        "0",
        None,
        0,
        "0",
        "Latvija",
        "",
        int(total.get("Balsstiesigie")),
        int(total.get("Nobalsojusie")),
        int(total.get("DerVelZimes")),
        lists={ballot[e.get("SarID")]: int(e.get("Zimes")) for e in total},
    )
    national = {ballot[e.get("SarID")]: int(e.get("Vietas")) for e in total}
    for e in base.find("Regioni"):
        code = str(int(e.get("RegID")) - 1)
        nodes[f"r{e.get('RegID')}"] = Node(
            f"r{e.get('RegID')}", "0", 1, code, clean(e.get("Nosaukums")), "", None, None, None
        )
    for e in base.find("PilsetasUnNovadi"):
        nodes[f"c{e.get('PilsNovID')}"] = Node(
            f"c{e.get('PilsNovID')}",
            f"r{e.get('RegID')}",
            2,
            e.get("PilsNovID"),
            clean(e.get("Nosaukums")),
            "",
            None,
            None,
            None,
        )
    for e in base.find("Iecirkni"):
        nodes[f"s{e.get('IecID')}"] = Node(
            f"s{e.get('IecID')}",
            f"c{e.get('PilsNovID')}",
            3,
            e.get("IecID"),
            clean(e.get("Nosaukums")),
            clean(e.get("Adrese")),
            None,
            None,
            None,
        )
    for e in root.find("PartijasRegionos"):
        nodes[f"r{e.get('RegID')}"].lists[ballot[e.get("SarID")]] = int(e.get("DerVelZimes"))
    for e in root.find("PartijasIecirknos"):
        nodes[f"s{e.get('IecID')}"].lists[ballot[e.get("SarID")]] = int(e.get("DerVelZimes"))
    return Reading(nodes, names, None, [], national)


def read(day: str) -> Reading:
    prefix = DAYS[day][1]
    if prefix == "sv2014":
        return _xml2014()
    nodes, names = _tree() if prefix == "epv2024" else _results(prefix)
    return Reading(nodes, names, _running(prefix), _elected(prefix))


def packages() -> list[str]:
    return ["lv.cvk/geo", "lv.cvk/registry", *(f"lv.cvk/{day}" for day in sorted([*DAYS, *REFERENDUMS, *MUNICIPAL]))]


def fetch() -> None:
    for name, url in URLS.items():
        raw.fetch(SOURCE, url, suffix=".xml" if name.endswith("xml") else ".json")


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        build(day).write()
    for day in REFERENDUMS:
        referendum(day).write()
    for day in MUNICIPAL:
        municipal(day).write()


def emit() -> None:
    package.run("load", packages())


def files(prefix: str) -> list[str]:
    return [name for name in URLS if name.startswith(prefix + "/")]


def geo() -> Package:
    p = Package(
        "lv.cvk/geo",
        SOURCE,
        "Latvia's polling-station and constituency schemes and the five Saeima constituencies, from the Central "
        "Election Commission",
    )
    p.external |= {LATVIA, COUNTRY}
    p.add("scheme", code="lv.apgabals", polity_id=LATVIA, kind="electoral_district", rank=1, noun="constituency")
    p.add("scheme", code="lv.iecirknis", polity_id=LATVIA, kind="precinct", rank=2, noun="polling station")
    for code, (short, native, english) in REGION_NAMES.items():
        id = area("lv.apgabals", code)
        p.add("area", id=id, scheme_code="lv.apgabals")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, native, "lv", "official")
        p.name(id, english, "en", "english", False)
    return p


def registry() -> Package:
    p = Package(
        "lv.cvk/registry",
        SOURCE,
        "Latvian parties, the Saeima, and the electoral systems, from the Central Election Commission's open data",
        depends=["lv.cvk/geo"],
    )
    p.external |= {LATVIA, COUNTRY}
    p.add(
        "source",
        id=CVK_SOURCE,
        kind="official_body",
        publisher="Centrālā vēlēšanu komisija",
        title="Election results and activity open data of the Central Election Commission of Latvia",
        url=PAGE,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Centrālā vēlēšanu komisija",
    )
    for name in URLS:
        p.artifact(path(name), CVK_SOURCE)
    p.add("body", id=SAEIMA, polity_id=LATVIA, kind="unicameral")
    p.name(SAEIMA, "Saeima", "en", "official")
    p.name(SAEIMA, "Latvijas Republikas Saeima", "lv", "native", False)
    for id, rules, text in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=LATVIA, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    labels: dict[str, str] = {}
    for day in DAYS:
        for label in read(day).names.values():
            labels.setdefault(party(label), label)
    for prefix in MUNICIPAL.values():
        for council in _municipal(prefix)[0].values():
            for label, _, _ in council.lists.values():
                labels.setdefault(party(label), label)
    for id, label in sorted(labels.items(), key=lambda item: item[1]):
        p.add("party", id=id, kind="bloc" if "apvienot" in label.casefold() else "party", polity_id=LATVIA)
        p.name(id, label, "lv", "official")
    return p


SYSTEM_SAEIMA = key("electoral_system", "saeima")
SYSTEM_EP = key("electoral_system", "ep")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")
SYSTEM_MUNICIPAL = key("electoral_system", "municipal")
SYSTEMS = (
    (
        SYSTEM_SAEIMA,
        {"formula": "sainte_lague", "threshold": {"percent": 5, "level": "list", "scope": "national"}},
        (
            "One hundred deputies are elected in five constituencies by list proportional representation with the "
            "Sainte-Laguë method. A list needs five percent of the valid votes nationwide to win seats. Voters mark "
            "candidates with plus and minus signs, which reorder the list."
        ),
    ),
    (
        SYSTEM_EP,
        {"formula": "sainte_lague", "threshold": {"percent": 5, "level": "list", "scope": "contest"}},
        (
            "Latvia forms one constituency for the European Parliament election. Seats are allocated to lists with "
            "the Sainte-Laguë method; a list needs five percent of the valid votes."
        ),
    ),
    (SYSTEM_REFERENDUM, {"formula": "referendum"}, "A yes or no question put to all voters."),
    (
        SYSTEM_MUNICIPAL,
        {"formula": "sainte_lague"},
        (
            "Each municipal council is elected in one constituency by list proportional representation with the "
            "Sainte-Laguë method. The thresholds are not stored here."
        ),
    ),
)


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def _title(day: str) -> tuple[str, str]:
    kind, _ = DAYS[day]
    year = int(day[:4])
    if kind == "ep":
        return f"{year} European Parliament election in Latvia", f"{year}. gada Eiropas Parlamenta vēlēšanas"
    number = {"2014-10-04": 12, "2018-10-06": 13, "2022-10-01": 14}[day]
    return f"{year} Latvian parliamentary election", f"{number}. Saeimas vēlēšanas"


def build(day: str) -> Package:
    kind, prefix = DAYS[day]
    first = date.fromisoformat(day)
    english, native = _title(day)
    p = Package(
        f"lv.cvk/{day}",
        SOURCE,
        f"The {english} by polling station from the Central Election Commission",
        depends=["lv.cvk/geo", "lv.cvk/registry"],
    )
    p.external |= {LATVIA, COUNTRY}
    for name in files(prefix):
        p.uses(path(name))
    r = read(day)
    election = key("election", day)
    where = f"lv/{day}-general"
    p.add("election", id=election, polity_id=LATVIA, kind="general", date_from=first, date_to=first, status="held")
    p.name(election, english, "en", "official")
    p.name(election, native, "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    if r.national:
        seats_text = "; ".join(f"{r.names[n]}: {v}" for n, v in sorted(r.national.items()) if v)
        p.add(
            "note",
            id=key("note", election, "seats"),
            entity_id=election,
            kind="caveat",
            body_md=(
                "The commission's results file prints seats only for the whole country, not for each "
                f"constituency, so no seats or elected candidates are stored per constituency. Seats by list: {seats_text}."
            ),
            lang="en",
        )
    stations = {n.id: n for n in r.nodes.values() if n.type == 3}
    regions = [n for n in r.nodes.values() if n.type == 1]
    root = next(n for n in r.nodes.values() if n.type == 0)
    scopes = sorted(regions, key=lambda n: int(n.code)) if kind == "sv" else [root]
    seats = Counter((e.region, e.list) for e in r.elected)
    units: dict[str, str] = {}
    for n in stations.values():
        code = f"{day}:{n.code}"
        unit = units[n.id] = key("unit", code)
        away = n.parent is not None and r.nodes[n.parent].name.casefold() == "ārzemes"
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=LATVIA,
            scheme_code="lv.iecirknis",
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="abroad_mission" if away else "standard",
        )
        text = ", ".join(part for part in (n.name, n.address) if part)
        if text:
            p.name(unit, text, "lv", "official")
    options: dict[tuple[str | None, int], str] = {}
    for scope in scopes:
        here = scope.id if kind == "sv" else None
        contest = key("contest", day, scope.code if kind == "sv" else "ep")
        if kind == "sv":
            _short, local, en = REGION_NAMES[scope.code]
            title, nativetitle, slug = f"{english}, {en}", f"{native}, {local}", REGION_SLUGS[scope.code]
        else:
            title, nativetitle, slug = english, native, "european-parliament"
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=SAEIMA if kind == "sv" else None,
            district_area_id=area("lv.apgabals", scope.code) if kind == "sv" else COUNTRY,
            magnitude=scope.seats if kind == "sv" else SEATS[day],
            round_number=1,
            electoral_system_id=SYSTEM_SAEIMA if kind == "sv" else SYSTEM_EP,
            default_result_kind="counted",
        )
        p.name(contest, title, "en", "official")
        p.name(contest, nativetitle, "lv", "native", False)
        p.add("slug", path=f"{where}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, scope.code if kind == "sv" else "ep")
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CVK_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        members = [n for n in stations.values() if kind != "sv" or (r.region(n) or scope) is scope]
        numbers = sorted(
            number
            for number in r.names
            if (r.running is None or (here, number) in r.running or scope.lists.get(number))
        )
        for number in numbers:
            option = options[(here, number)] = key("option", day, scope.code if kind == "sv" else "ep", number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(r.names[number]),
                ballot_position=number,
                ballot_label=r.names[number],
            )
            if seats[(here, number)]:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats[(here, number)])
        for n in members:
            _put(p, result_set, units[n.id], None, REGISTERED, n.voters)
            _put(p, result_set, units[n.id], None, CAST, n.cast)
            _put(p, result_set, units[n.id], None, VALID, n.valid)
            for number in numbers:
                _put(p, result_set, units[n.id], options[(here, number)], VOTES, n.lists.get(number, 0))
        printed = any(n.voters is not None for n in members)
        for measure, value in (
            (REGISTERED, scope.voters if printed else None),
            (CAST, scope.cast),
            (VALID, scope.valid),
        ):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for number in numbers:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=options[(here, number)],
                measure=VOTES,
                value=scope.lists.get(number, 0),
            )
    for e in r.elected:
        here = e.region if kind == "sv" else None
        option = options[(here, e.list)]
        person = key("person", day, e.id)
        p.add("person", id=person)
        p.name(person, f"{e.name} {e.surname}", "lv", "official")
        candidacy = key("candidacy", day, e.id)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="list_member",
            list_position=e.position,
            ballot_name=f"{e.name} {e.surname}",
        )
        p.add(
            "outcome",
            result_set_id=_rs_of(p, option),
            option_id=option,
            candidacy_id=candidacy,
            kind="elected",
        )
    return p


def _rs_of(p: Package, option: str) -> str:
    contest = next(o["contest_id"] for o in p.rows["contest_option"] if o["id"] == option)
    return next(r["id"] for r in p.rows["result_set"] if r["contest_id"] == contest)


REFERENDUM_TITLES = {
    "2011-07-23": (
        "2011 Latvian referendum on the dissolution of the Saeima",
        "2011. gada tautas nobalsošana par 10. Saeimas atlaišanu",
        "referendum",
    ),
    "2012-02-18": (
        "2012 Latvian referendum on Russian as a second official language",
        "2012. gada tautas nobalsošana par grozījumiem Satversmē (valodas referendums)",
        "initiative",
    ),
}
REFERENDUM_METHOD = (
    "Counted per polling station and published by the Central Election Commission of Latvia as open data. "
    "Registered voters are the voters attached to the polling station in the commission's file. Voters are those who "
    "took part, ballots cast are the ballot papers counted, and invalid ballots are those declared invalid. The "
    "file also prints envelope counts and the numbers of voters who did not take part; they are not stored here."
)


def _referendum(day: str) -> tuple[list[dict[str, str]], list[dict[str, int]], dict[str, int]]:
    root = ET.parse(
        path(f"{REFERENDUMS[day]}/" + ("results.xml" if day == "2011-07-23" else "results_tn2012.xml")).path
    ).getroot()

    def values(e: ET.Element) -> dict[str, int]:
        return {v.get("id"): int(v.text) for v in e.findall("value")}

    stations = [dict(e.attrib) for e in root.findall("district")]
    figures = [values(e) for e in root.findall("district")]
    if day == "2011-07-23":
        names = [values(e) for e in root.findall("area")]
        total = {k: sum(a[k] for a in names) for k in names[0]}
    else:
        total = values(root.find("area"))
    return stations, figures, total


def referendum(day: str) -> Package:
    first = date.fromisoformat(day)
    english, native, kind = REFERENDUM_TITLES[day]
    p = Package(
        f"lv.cvk/{day}",
        SOURCE,
        f"The {english} by polling station from the Central Election Commission",
        depends=["lv.cvk/geo", "lv.cvk/registry"],
    )
    p.external |= {LATVIA, COUNTRY}
    for name in files(REFERENDUMS[day]):
        p.uses(path(name))
    election = key("election", day)
    where = f"lv/{day}-referendum"
    p.add("election", id=election, polity_id=LATVIA, kind="referendum", date_from=first, date_to=first, status="held")
    p.name(election, english, "en", "official")
    p.name(election, native, "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    contest = key("contest", day, "question")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        district_area_id=COUNTRY,
        round_number=1,
        electoral_system_id=SYSTEM_REFERENDUM,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.name(contest, native, "lv", "native", False)
    p.add("slug", path=f"{where}/question", entity_id=contest, is_current=True)
    p.add("ballot_question", contest_id=contest, type=kind)
    result_set = key("result_set", day, "question")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CVK_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=REFERENDUM_METHOD,
        lang="en",
    )
    answers = {}
    for position, (column, label, english_label) in enumerate((("f2", "Par", "For"), ("f3", "Pret", "Against")), 1):
        option = answers[column] = key("option", day, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=f"{label} ({english_label})",
        )
    stations, figures, total = _referendum(day)
    over = 0
    for station, row in zip(stations, figures, strict=True):
        code = f"{day}:{station['id']}"
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=LATVIA,
            scheme_code="lv.iecirknis",
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        text = ", ".join(part for part in (clean(station.get("name")), clean(station.get("address"))) if part)
        if text:
            p.name(unit, text, "lv", "official")
        for measure, column in ((REGISTERED, "c"), (14, "c3"), (CAST, "e"), (5, "f1")):
            _put(p, result_set, unit, None, measure, row[column])
        for column, option in answers.items():
            _put(p, result_set, unit, option, VOTES, row[column])
        over += row["c3"] > row["c"]
    for measure, column in ((REGISTERED, "c"), (14, "c3"), (CAST, "e"), (5, "f1")):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total[column])
    for column, option in answers.items():
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=total[column])
    if over:
        p.accept(
            "accounting.turnout",
            f"{over} polling stations print more voters than the voters attached to them; stored as printed",
        )
        p.add(
            "note",
            id=key("note", result_set, "turnout"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"At {over} polling stations, more voters took part than the voters attached to the station in "
            "the commission's file. The figures are stored as printed.",
            lang="en",
        )
    return p


MUNICIPAL = {"2017-06-03": "pv2017"}
MUNICIPAL_METHOD = (
    "Counted per polling station and published by the Central Election Commission of Latvia as open data. "
    "Registered voters are the voters attached to the polling station in the commission's file, ballots cast are "
    "the voters who took part, and valid votes are the valid marks for lists. Each council is elected in its own "
    "constituency; its seats are allocated to lists, and the commission's elected-candidate file names the "
    "members. The file also holds each candidate's plus and minus marks and valid-envelope counts; they are not "
    "stored here yet."
)


@dataclass
class Council:
    id: str
    name: str
    voters: int | None
    cast: int | None
    valid: int | None
    seats: int | None
    lists: dict[str, tuple[str, int, int]] = field(default_factory=dict)


@dataclass
class Booth:
    id: str
    number: str
    name: str
    address: str
    voters: int | None
    cast: int | None
    valid: int | None
    lists: dict[str, int] = field(default_factory=dict)


def _int(e: ET.Element, tag: str) -> int | None:
    found = e.find(f"{tag}/Count")
    if found is None:
        found = e.find(tag)
    return int(found.text) if found is not None and found.text else None


def _municipal(prefix: str) -> tuple[dict[str, Council], list[Booth], dict[str, str]]:
    councils: dict[str, Council] = {}
    booths: list[Booth] = []
    for _, e in ET.iterparse(path(f"{prefix}/electionresults636363813214220000.xml").path, events=("end",)):
        if e.tag != "DepartmentResultModel":
            continue
        kind = e.findtext("Type")
        if kind == "2":
            council = Council(
                e.findtext("Id"),
                clean(e.findtext("Name")),
                _int(e, "VoterCount"),
                _int(e, "VotedCount"),
                _int(e, "ValidMarkCount"),
                _int(e, "MaxElectedCandidateCount"),
            )
            for item in e.iter("CandidateListResultModel"):
                council.lists[item.findtext("CandidateListId")] = (
                    clean(item.findtext("CandidateListName")),
                    _int(item, "ValidMarkCount") or 0,
                    _int(item, "ElectedCandidateCount") or 0,
                )
            councils[council.id] = council
        elif kind == "3":
            booth = Booth(
                e.findtext("Id"),
                e.findtext("Number") or "",
                clean(e.findtext("Name")),
                clean(e.findtext("Address")),
                _int(e, "VoterCount"),
                _int(e, "VotedCount"),
                _int(e, "ValidMarkCount"),
            )
            for item in e.iter("CandidateListResultModel"):
                booth.lists[item.findtext("CandidateListId")] = _int(item, "ValidMarkCount") or 0
            booths.append(booth)
        e.clear()
    owner = {list_id: council.id for council in councils.values() for list_id in council.lists}
    return councils, booths, owner


def _members(prefix: str) -> list[dict[str, str]]:
    root = ET.parse(path(f"{prefix}/electedcandidates636363813214220000.xml").path).getroot()
    return [
        {
            "council": m.findtext("DepartmentId"),
            "list": m.findtext("CandidateListId"),
            "id": m.findtext("CandidateId"),
            "name": clean(f"{m.findtext('Forename')} {m.findtext('Lastname')}"),
            "number": m.findtext("NumberInList"),
        }
        for m in root.iter("ElectedCandidateModel")
    ]


def municipal(day: str) -> Package:
    prefix = MUNICIPAL[day]
    first = date.fromisoformat(day)
    year = first.year
    english, native = f"{year} Latvian municipal elections", f"{year}. gada pašvaldību vēlēšanas"
    p = Package(
        f"lv.cvk/{day}",
        SOURCE,
        f"The {english} by polling station from the Central Election Commission",
        depends=["lv.cvk/geo", "lv.cvk/registry"],
    )
    p.external |= {LATVIA, COUNTRY}
    for name in files(prefix):
        p.uses(path(name))
    councils, booths, owner = _municipal(prefix)
    election = key("election", day)
    where = f"lv/{day}-local"
    p.add("election", id=election, polity_id=LATVIA, kind="local", date_from=first, date_to=first, status="held")
    p.name(election, english, "en", "official")
    p.name(election, native, "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    sets: dict[str, str] = {}
    options: dict[tuple[str, str], str] = {}
    used: Counter[str] = Counter()
    for council in sorted(councils.values(), key=lambda c: c.name):
        contest = key("contest", day, council.id)
        slug = changeset.slug(council.name)
        used[slug] += 1
        slug = slug if used[slug] == 1 else f"{slug}-{used[slug]}"
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            magnitude=council.seats,
            round_number=1,
            electoral_system_id=SYSTEM_MUNICIPAL,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} {council.name} council election", "en", "official")
        p.name(contest, f"{council.name} domes vēlēšanas {year}", "lv", "native", False)
        p.add("slug", path=f"{where}/{slug}", entity_id=contest, is_current=True)
        result_set = sets[council.id] = key("result_set", day, council.id)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CVK_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=MUNICIPAL_METHOD,
            lang="en",
        )
        for position, (list_id, (label, marks, won)) in enumerate(
            sorted(council.lists.items(), key=lambda item: item[1][0]), 1
        ):
            option = options[(council.id, list_id)] = key("option", day, council.id, list_id)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(label),
                ballot_position=None,
                ballot_label=label,
            )
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=marks)
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
        for measure, value in ((REGISTERED, council.voters), (CAST, council.cast), (VALID, council.valid)):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    over = 0
    for booth in booths:
        council = owner[next(iter(booth.lists))]
        result_set = sets[council]
        code = f"{day}:{booth.number}:{booth.id}"
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=LATVIA,
            scheme_code="lv.iecirknis",
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        text = ", ".join(part for part in (booth.name, booth.address) if part)
        if text:
            p.name(unit, text, "lv", "official")
        _put(p, result_set, unit, None, REGISTERED, booth.voters)
        _put(p, result_set, unit, None, CAST, booth.cast)
        _put(p, result_set, unit, None, VALID, booth.valid)
        for list_id, marks in booth.lists.items():
            _put(p, result_set, unit, options[(council, list_id)], VOTES, marks)
        over += (booth.cast or 0) > (booth.voters or 0)
    for m in _members(prefix):
        option = options[(m["council"], m["list"])]
        person = key("person", day, m["council"], m["id"])
        p.add("person", id=person)
        p.name(person, m["name"], "lv", "official")
        candidacy = key("candidacy", day, m["council"], m["id"])
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="list_member",
            list_position=int(m["number"]),
            ballot_name=m["name"],
        )
        p.add("outcome", result_set_id=sets[m["council"]], option_id=option, candidacy_id=candidacy, kind="elected")
    if over:
        p.accept(
            "accounting.turnout",
            f"{over} polling stations print more voters than the voters attached to them; stored as printed",
        )
    return p
