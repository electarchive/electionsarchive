import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any
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


def loaded(name: str) -> Any:
    return json.loads(path(name).path.read_text(encoding="utf-8-sig"))


def _attr(element: ET.Element, name: str) -> str:
    found = element.get(name)
    if found is None:
        raise LookupError(f"{element.tag} has no {name}")
    return found


def _child(element: ET.Element, tag: str) -> ET.Element:
    found = element.find(tag)
    if found is None:
        raise LookupError(f"{element.tag} has no {tag}")
    return found


def _text(element: ET.Element, tag: str) -> str:
    found = element.findtext(tag)
    if found is None:
        raise LookupError(f"{element.tag} has no {tag}")
    return found


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

    def walk(item: Any, parent: str | None) -> None:
        stats = item["departmentStats"]
        id = str(item["alias"])
        lists: dict[int, int] = {}
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
    base = _child(root, "PamatTabulas")
    nodes: dict[str, Node] = {}
    names: dict[int, str] = {}
    ballot: dict[str, int] = {}
    for entry in _child(base, "Saraksti"):
        ballot[_attr(entry, "SarID")] = int(_attr(entry, "Loze"))
        names[int(_attr(entry, "Loze"))] = clean(_attr(entry, "Nosaukums"))
    total = _child(root, "Valsts")
    nodes["0"] = Node(
        "0",
        None,
        0,
        "0",
        "Latvija",
        "",
        int(_attr(total, "Balsstiesigie")),
        int(_attr(total, "Nobalsojusie")),
        int(_attr(total, "DerVelZimes")),
        lists={ballot[_attr(e, "SarID")]: int(_attr(e, "Zimes")) for e in total},
    )
    national = {ballot[_attr(e, "SarID")]: int(_attr(e, "Vietas")) for e in total}
    for e in _child(base, "Regioni"):
        region = _attr(e, "RegID")
        nodes[f"r{region}"] = Node(
            f"r{region}", "0", 1, str(int(region) - 1), clean(_attr(e, "Nosaukums")), "", None, None, None
        )
    for e in _child(base, "PilsetasUnNovadi"):
        town = _attr(e, "PilsNovID")
        nodes[f"c{town}"] = Node(
            f"c{town}",
            f"r{_attr(e, 'RegID')}",
            2,
            town,
            clean(_attr(e, "Nosaukums")),
            "",
            None,
            None,
            None,
        )
    for e in _child(base, "Iecirkni"):
        station = _attr(e, "IecID")
        nodes[f"s{station}"] = Node(
            f"s{station}",
            f"c{_attr(e, 'PilsNovID')}",
            3,
            station,
            clean(_attr(e, "Nosaukums")),
            clean(_attr(e, "Adrese")),
            None,
            None,
            None,
        )
    for e in _child(root, "PartijasRegionos"):
        nodes[f"r{_attr(e, 'RegID')}"].lists[ballot[_attr(e, "SarID")]] = int(_attr(e, "DerVelZimes"))
    for e in _child(root, "PartijasIecirknos"):
        nodes[f"s{_attr(e, 'IecID')}"].lists[ballot[_attr(e, "SarID")]] = int(_attr(e, "DerVelZimes"))
    return Reading(nodes, names, None, [], national)


def read(day: str) -> Reading:
    prefix = DAYS[day][1]
    if prefix == "sv2014":
        return _xml2014()
    nodes, names = _tree() if prefix == "epv2024" else _results(prefix)
    return Reading(nodes, names, _running(prefix), _elected(prefix))


def packages() -> list[str]:
    return [
        "lv.cvk/geo",
        "lv.cvk/registry",
        *(f"lv.cvk/{day}" for day in sorted([*DAYS, *REFERENDUMS, *MUNICIPAL, *DATI, "2021-06-05", *LEGACY])),
        *(f"lv.cvk/tn-{day}" for day in TN),
    ]


def fetch() -> None:
    for name, url in URLS.items():
        raw.fetch(SOURCE, url, suffix=".xml" if name.endswith("xml") else ".json")
    for code in DATI.values():
        root = dati_url(code)
        raw.fetch(SOURCE, root, suffix=".html")
        for url in (*dati_pages(code)[1], *dati_pages(code)[2]):
            raw.fetch(SOURCE, url, suffix=".html")
        raw.fetch(SOURCE, f"{HOST}/{code}/ieveletie-deputati/", suffix=".html")
    for day in LEGACY:
        raw.fetch(SOURCE, legacy_url(day), suffix=".html")
    for url in tn_urls():
        raw.fetch(SOURCE, url, suffix=".html")
    raw.fetch(SOURCE, PV2021, suffix=".html")
    raw.fetch(SOURCE, PV2021_ELECTED, suffix=".html")
    for url in pv2021_pages():
        raw.fetch(SOURCE, url, suffix=".html")


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        build(day).write()
    for day in REFERENDUMS:
        referendum(day).write()
    for day in MUNICIPAL:
        municipal(day).write()
    for day in DATI:
        build_dati(day).write()
    build_pv2021().write()
    for day in LEGACY:
        build_legacy(day).write()
    for day in TN:
        referendum_archive(day).write()


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
    p.add("scheme", code="lv.valsts", polity_id=LATVIA, kind="administrative", rank=0, noun="country")
    p.add("scheme", code="lv.apgabals", polity_id=LATVIA, kind="electoral_district", rank=1, noun="constituency")
    p.add("scheme", code="lv.pasvaldiba", polity_id=LATVIA, kind="administrative", rank=2, noun="municipality")
    p.add("scheme", code="lv.iecirknis", polity_id=LATVIA, kind="precinct", rank=3, noun="polling station")
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
    for day in LEGACY:
        p.artifact(raw.cached(SOURCE, legacy_url(day)), CVK_SOURCE)
    for url in (PV2021, PV2021_ELECTED, *pv2021_pages()):
        p.artifact(raw.cached(SOURCE, url), CVK_SOURCE)
    for code in DATI.values():
        for url in (
            *dati_pages(code)[0],
            *dati_pages(code)[1],
            *dati_pages(code)[2],
            f"{HOST}/{code}/ieveletie-deputati/",
        ):
            p.artifact(raw.cached(SOURCE, url), CVK_SOURCE)
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
    for code in DATI.values():
        data = read_dati(code)
        for place in (*data.regions.values(), *data.towns.values()):
            for stand in place.stands.values():
                labels.setdefault(party(stand.name), stand.name)
    for day in LEGACY:
        for label in read_legacy(day).lists.values():
            labels.setdefault(party(label), label)
    for page in read_pv2021()[0]:
        for stand in page.stands.values():
            labels.setdefault(party(stand.name), stand.name)
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
    return str(next(r["id"] for r in p.rows["result_set"] if r["contest_id"] == contest))


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
        return {_attr(v, "id"): int(v.text or 0) for v in e.findall("value")}

    stations = [dict(e.attrib) for e in root.findall("district")]
    figures = [values(e) for e in root.findall("district")]
    if day == "2011-07-23":
        names = [values(e) for e in root.findall("area")]
        total = {k: sum(a[k] for a in names) for k in names[0]}
    else:
        total = values(_child(root, "area"))
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


# The commission's archive pages for the referendums before 2011. Only the 2003 page prints figures below the
# national totals, by city, district, and abroad; the others print the national totals in text.
TN_HOST = "https://arhivs.cvk.lv/lv"
TN_ROOT = f"{TN_HOST}/notikusas-tautas-nobalsosanas/"
TN_RESULTS_2003 = f"{TN_HOST}/tn-2003-rezultati/"
TN_SLUGS = {
    "1998-10-03": "par-likuma-grozijumi-pilsonibas-likuma-atcelsanu-1998",
    "1999-11-13": "par-likuma-grozijumi-pensiju-likuma-atcelsanu-1999",
    "2003-09-20": "par-latvijas-dalibu-eiropas-savieniba-2003",
    "2007-07-07": "par-grozijumu-nacionalas-drosibas-likuma-un-valsts-drosibas-iestazu-likuma-atcelsanu-2007",
    "2008-08-02": "par-grozijumiem-satversme-2008",
    "2008-08-23": "par-grozijumu-likuma-par-valsts-pensijam-2008",
}
TN_AGAINST = ("Pret", "Against")
TN_FOR = ("Par", "For")
TN_FOR_REPEAL = ("Par atcelšanu", "For repeal")
TN_AGAINST_REPEAL = ("Pret atcelšanu", "Against repeal")


@dataclass
class Question:
    slug: str
    english: str
    native: str
    type: str
    answers: tuple[tuple[str, str], tuple[str, str]]
    figures: dict[int, int]  # measure -> national total printed in the page's text
    votes: tuple[int, int] | None = None  # for and against, where printed


@dataclass
class Vote:
    english: str
    native: str
    questions: list[Question]


TN: dict[str, Vote] = {
    "1998-10-03": Vote(
        "1998 Latvian referendum on the repeal of the amendments to the Citizenship Law",
        "1998. gada tautas nobalsošana par likuma „Grozījumi Pilsonības likumā” atcelšanu",
        [
            Question(
                "question",
                "1998 Latvian referendum on the repeal of the amendments to the Citizenship Law",
                "1998. gada tautas nobalsošana par likuma „Grozījumi Pilsonības likumā” atcelšanu",
                "optional_referendum",
                (TN_FOR_REPEAL, TN_AGAINST_REPEAL),
                {14: 928040},
                (416584, 487559),
            )
        ],
    ),
    "1999-11-13": Vote(
        "1999 Latvian referendum on the repeal of the amendments to the Law on State Pensions",
        "1999. gada tautas nobalsošana par likuma „Grozījumi likumā „Par valsts pensijām”” atcelšanu",
        [
            Question(
                "question",
                "1999 Latvian referendum on the repeal of the amendments to the Law on State Pensions",
                "1999. gada tautas nobalsošana par likuma „Grozījumi likumā „Par valsts pensijām”” atcelšanu",
                "optional_referendum",
                (TN_FOR_REPEAL, TN_AGAINST_REPEAL),
                {14: 339879},
                (320071, 18160),
            )
        ],
    ),
    "2003-09-20": Vote(
        "2003 Latvian referendum on membership of the European Union",
        "2003. gada tautas nobalsošana par Latvijas dalību Eiropas Savienībā",
        [
            Question(
                "question",
                "2003 Latvian referendum on membership of the European Union",
                "2003. gada tautas nobalsošana par Latvijas dalību Eiropas Savienībā",
                "mandatory_referendum",
                (TN_FOR, TN_AGAINST),
                {},
            )
        ],
    ),
    "2007-07-07": Vote(
        "2007 Latvian referendum on the repeal of the amendments to the National Security Law and the State "
        "Security Institutions Law",
        "2007. gada tautas nobalsošana par grozījumu Nacionālās drošības likumā un Valsts drošības iestāžu "
        "likumā atcelšanu",
        [
            Question(
                "national-security-law",
                "2007 Latvian referendum on the repeal of the amendments to the National Security Law",
                "2007. gada tautas nobalsošana par likuma „Grozījumi Nacionālās drošības likumā” atcelšanu",
                "optional_referendum",
                (TN_FOR_REPEAL, TN_AGAINST_REPEAL),
                {3: 338348},
            ),
            Question(
                "state-security-institutions-law",
                "2007 Latvian referendum on the repeal of the amendments to the State Security Institutions Law",
                "2007. gada tautas nobalsošana par likuma „Grozījumi Valsts drošības iestāžu likumā” atcelšanu",
                "optional_referendum",
                (TN_FOR_REPEAL, TN_AGAINST_REPEAL),
                {3: 338342},
            ),
        ],
    ),
    "2008-08-02": Vote(
        "2008 Latvian referendum on the Satversme amendments bill on petitions to dissolve the Saeima",
        "2008. gada tautas nobalsošana par likumprojektu „Grozījumi Latvijas Republikas Satversmē”",
        [
            Question(
                "question",
                "2008 Latvian referendum on the Satversme amendments bill on petitions to dissolve the Saeima",
                "2008. gada tautas nobalsošana par likumprojektu „Grozījumi Latvijas Republikas Satversmē”",
                "initiative",
                (TN_FOR, TN_AGAINST),
                {14: 629304, 3: 629119, 5: 1415},
                (608847, 18857),
            )
        ],
    ),
    "2008-08-23": Vote(
        "2008 Latvian referendum on the state pensions bill",
        "2008. gada tautas nobalsošana par likumprojektu „Grozījums likumā „Par valsts pensijām””",
        [
            Question(
                "question",
                "2008 Latvian referendum on the state pensions bill",
                "2008. gada tautas nobalsošana par likumprojektu „Grozījums likumā „Par valsts pensijām””",
                "initiative",
                (TN_FOR, TN_AGAINST),
                {14: 346846, 3: 346784, 5: 794},
                (334429, 11561),
            )
        ],
    ),
}
TN_METHOD = (
    "Published by the Central Election Commission of Latvia on its archive site. The commission prints only the "
    "national totals for this referendum, in the text of its page, so the whole country is the finest unit "
    "stored. {figures}"
)
TN_METHOD_2003 = (
    "Published by the Central Election Commission of Latvia on its archive site as a table for each city, each "
    "district (rajons), and the polling stations abroad; no polling-station figures are published. Voters are the "
    "voters who took part, and ballots cast are the ballot papers counted. The table also splits voters and "
    "ballots between the polling station and the voter's location; the split is not stored here."
)
TN_FIGURES = {
    14: "Voters are the voters who took part.",
    3: "Ballots cast are the ballot papers found in the ballot boxes; the commission counts them as the voters who "
    "took part.",
    5: "Invalid ballots are those marked for both answers or for neither.",
}


def tn_url(day: str) -> str:
    return f"{TN_HOST}/{TN_SLUGS[day]}/"


def tn_urls() -> list[str]:
    return [TN_ROOT, *(tn_url(day) for day in TN_SLUGS), TN_RESULTS_2003]


def tn_text(url: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
    return re.sub(r"[\s ]+", "", text)


def tn_check(day: str, question: Question) -> None:
    """Fail unless every figure stored from a page's text is printed on that page."""
    text = tn_text(tn_url(day))
    for number in (*question.figures.values(), *(question.votes or ())):
        if str(number) not in text:
            raise ValueError(f"{day}: {number} is not printed on {tn_url(day)}")


@dataclass
class Territory:
    name: str
    voters: int
    ballots: int
    votes: tuple[int, int]
    invalid: int


def tn_2003() -> tuple[list[Territory], Territory]:
    page = tables(TN_RESULTS_2003)
    rows = next(rows for rows in page.tables.values() if rows[0][0].text.startswith("Teritorija"))
    head = [cell.text for cell in rows[0]]
    column = {name: index for index, name in enumerate(head)}
    found: list[Territory] = []
    for row in rows[1:]:
        found.append(
            Territory(
                clean(re.sub(r"\s*\(\d+\)\s*$", "", row[0].text)),
                _n(row[column["Balsotāju kopskaits"]]),
                _n(row[column["Balsošanas zīmju kopskaits"]]),
                (_n(row[column["Par"]]), _n(row[column["Pret"]])),
                _n(row[column["Nederīgas *"]]),
            )
        )
    total = found.pop(0)
    if total.name != "KOPĀ":
        raise ValueError(f"the first row of the 2003 table is {total.name}, not the total")
    for field_ in ("voters", "ballots", "invalid"):
        if sum(getattr(t, field_) for t in found) != getattr(total, field_):
            raise ValueError(f"the 2003 {field_} do not sum to the printed total")
    for side in (0, 1):
        if sum(t.votes[side] for t in found) != total.votes[side]:
            raise ValueError("the 2003 votes do not sum to the printed total")
    headline = {676700, 325980, 7787, 1010467}
    if headline != {total.votes[0], total.votes[1], total.invalid, total.ballots}:
        raise ValueError("the 2003 table's total differs from the headline results")
    return found, total


def referendum_archive(day: str) -> Package:
    vote = TN[day]
    first = date.fromisoformat(day)
    p = Package(
        f"lv.cvk/tn-{day}",
        SOURCE,
        f"The {vote.english} from the Central Election Commission's archive",
        depends=["lv.cvk/geo", "lv.cvk/registry"],
    )
    p.external |= {LATVIA, COUNTRY}
    page = raw.cached(SOURCE, tn_url(day))
    p.artifact(page, CVK_SOURCE)
    p.uses(raw.cached(SOURCE, TN_ROOT))
    if day == "2003-09-20":
        p.artifact(raw.cached(SOURCE, TN_RESULTS_2003), CVK_SOURCE)
        territories, total = tn_2003()
    election = key("election", day, "tn")
    where = f"lv/{day}-referendum"
    p.add("election", id=election, polity_id=LATVIA, kind="referendum", date_from=first, date_to=first, status="held")
    p.name(election, vote.english, "en", "official")
    p.name(election, vote.native, "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    for question in vote.questions:
        contest = key("contest", day, "tn", question.slug)
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
        p.name(contest, question.english, "en", "official")
        p.name(contest, question.native, "lv", "native", False)
        p.add("slug", path=f"{where}/{question.slug}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type=question.type)
        result_set = key("result_set", day, "tn", question.slug)
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
        if day == "2003-09-20":
            method = TN_METHOD_2003
        else:
            method = TN_METHOD.format(
                figures=" ".join(TN_FIGURES[m] for m in sorted(question.figures, key=lambda m: (m != 14, m)))
            )
            if question.votes is None:
                method += " The page does not print the votes for and against."
            tn_check(day, question)
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        options = []
        for position, (label, english_label) in enumerate(question.answers, 1):
            option = key("option", day, "tn", question.slug, position)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=f"{label} ({english_label})",
            )
        if day == "2003-09-20":
            for territory in territories:
                code = f"{day}:tn:{territory.name}"
                unit = key("unit", code)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=LATVIA,
                    scheme_code="lv.pasvaldiba",
                    code=code,
                    area_relation="unlocated",
                    area_id=None,
                    kind="standard",
                )
                p.name(unit, territory.name, "lv", "official")
                for measure, value in ((14, territory.voters), (CAST, territory.ballots), (5, territory.invalid)):
                    _put(p, result_set, unit, None, measure, value)
                for option, value in zip(options, territory.votes, strict=True):
                    _put(p, result_set, unit, option, VOTES, value)
            totals = {14: total.voters, CAST: total.ballots, 5: total.invalid}
            national = dict(zip(options, total.votes, strict=True))
        else:
            code = f"{day}:tn:{question.slug}:valsts"
            unit = key("unit", code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=LATVIA,
                scheme_code="lv.valsts",
                code=code,
                area_relation="equals",
                area_id=COUNTRY,
                kind="aggregate",
            )
            p.name(unit, "Latvija", "lv", "official")
            totals = question.figures
            national = dict(zip(options, question.votes or (), strict=False))
            for measure, value in totals.items():
                _put(p, result_set, unit, None, measure, value)
            for option, value in national.items():
                _put(p, result_set, unit, option, VOTES, value)
        for measure, value in totals.items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in national.items():
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
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
            "council": _text(m, "DepartmentId"),
            "list": _text(m, "CandidateListId"),
            "id": _text(m, "CandidateId"),
            "name": clean(f"{_text(m, 'Forename')} {_text(m, 'Lastname')}"),
            "number": _text(m, "NumberInList"),
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


DATI = {"2026-10-03": "SV2026", "2025-06-07": "PV2025"}
HOST = "https://dati.cvk.lv"


class Cell:
    def __init__(self, text: str, order: str | None, href: str | None) -> None:
        self.text = text
        self.order = order
        self.href = href


class Tables(HTMLParser):
    """The results tables of a dati.cvk.lv page, by table id, as rows of cells."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: dict[str, list[list[Cell]]] = {}
        self.links: list[str] = []
        self.anchors: list[tuple[str, str]] = []
        self._anchor: tuple[str, list[str]] | None = None
        self._table: list[list[Cell]] | None = None
        self._row: list[Cell] | None = None
        self._cell: list[str] | None = None
        self._order: str | None = None
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "a" and found.get("href"):
            self.links.append(found["href"] or "")
            self._anchor = (found["href"] or "", [])
            if self._cell is not None and self._href is None:
                self._href = found["href"]
        elif tag == "table":
            self._table = self.tables.setdefault(found.get("id") or f"t{len(self.tables)}", [])
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell, self._order, self._href = [], found.get("data-order"), None

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None:
            self.anchors.append((self._anchor[0], clean("".join(self._anchor[1]))))
            self._anchor = None
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(Cell(clean("".join(self._cell)), self._order, self._href))
            self._cell = None
        elif tag == "tr" and self._table is not None and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table":
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor[1].append(data)
        if self._cell is not None:
            self._cell.append(data)


def tables(url: str) -> Tables:
    parser = Tables()
    parser.feed(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
    return parser


def dati_url(code: str, *parts: str) -> str:
    return f"{HOST}/{code}/velesanu-rezultati/" + "".join(f"{part}/" for part in parts)


def dati_pages(code: str) -> tuple[list[str], list[str], list[str]]:
    """The commission's results pages for an election: the root, the region pages, and the municipality pages."""
    root = tables(dati_url(code))
    seen: dict[tuple[str, ...], None] = {}
    for link in root.links:
        found = re.fullmatch(r"(?:\.\./)+velesanu-rezultati/([^/]+)(?:/([^/]+))?/?", link)
        if found and found.group(1) != "kandidatu-saraksti":
            seen[tuple(part for part in found.groups() if part)] = None
    regions = [parts[0] for parts in seen if len(parts) == 1]
    towns = [(parts[0], parts[1]) for parts in seen if len(parts) == 2]
    return [dati_url(code)], [dati_url(code, region) for region in regions], [dati_url(code, *t) for t in towns]


def _n(cell: Cell) -> int:
    return int(cell.order if cell.order and re.fullmatch(r"-?\d+", cell.order) else re.sub(r"\D", "", cell.text) or 0)


@dataclass
class Stand:
    """A list's printed total in one contest: its name, valid marks, and seats."""

    name: str
    votes: int
    seats: int


@dataclass
class Place:
    slug: str
    name: str
    stations: list[tuple[int, str, int, int, dict[int, int]]] = field(default_factory=list)
    stands: dict[int, Stand] = field(default_factory=dict)
    row: tuple[int, int, int, int] | None = None


@dataclass
class Dati:
    regions: dict[str, Place]
    towns: dict[tuple[str, str], Place]
    elected: list[tuple[str, str, str, int | None]]


def _stands(page: Tables) -> dict[int, Stand]:
    found: dict[int, Stand] = {}
    for row in page.tables.get("result-candidate-table", [])[1:]:
        if len(row) >= 3 and row[0].order and row[0].order.isdigit():
            found[int(row[0].order)] = Stand(
                clean(row[1].order or row[1].text), _n(row[2]), _n(row[4]) if len(row) > 4 and row[4].text else 0
            )
    return found


def _rows(page: Tables) -> dict[str, tuple[str, tuple[int, int, int, int]]]:
    """The rows of a place's statistics table, by link slug: name and registered, voted, envelopes, marks."""
    found: dict[str, tuple[str, tuple[int, int, int, int]]] = {}
    for row in page.tables.get("result-stat-table", [])[1:]:
        if len(row) == 5 and row[0].href:
            slug = row[0].href.rstrip("/").rsplit("/", 1)[-1]
            found[slug] = (row[0].text, (_n(row[1]), _n(row[2]), _n(row[3]), _n(row[4])))
    return found


def read_dati(code: str) -> Dati:
    _, region_urls, town_urls = dati_pages(code)
    regions: dict[str, Place] = {}
    towns: dict[tuple[str, str], Place] = {}
    names = {link.rstrip("/").rsplit("/", 1)[-1]: text for link, text in tables(dati_url(code)).anchors if text}
    summary = _rows(tables(dati_url(code)))
    for url in region_urls:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        page = tables(url)
        place = regions[slug] = Place(slug, names[slug], stands=_stands(page), row=summary.get(slug, (slug, None))[1])
        for town, (_, row) in _rows(page).items():
            towns[(slug, town)] = Place(town, names.get(town, town), row=row)
    for url in town_urls:
        region, slug = url.rstrip("/").rsplit("/", 2)[-2:]
        page = tables(url)
        place = towns[(region, slug)]
        place.stands = _stands(page)
        stats = [row for row in page.tables["result-stat-table"][1:] if row[0].order and row[0].order.isdigit()]
        header = [cell.text for cell in page.tables["result-candidates-list-table"][0]]
        matrix = {
            row[0].order: row
            for row in page.tables["result-candidates-list-table"][1:]
            if row[0].order and row[0].order.isdigit()
        }
        for row in stats:
            order = row[0].order or ""
            votes = {
                int(number): _n(cell)
                for number, cell in zip(header[2:], matrix[order][2:], strict=False)
                if number.isdigit()
            }
            place.stations.append((int(order), row[1].text, _n(row[2]), _n(row[4]), votes))
    elected = []
    for row in tables(f"{HOST}/{code}/ieveletie-deputati/").tables["elected-total-table"][1:]:
        elected.append(
            (
                row[0].text,
                clean(row[1].order or row[1].text),
                row[2].text,
                int(row[3].text) if len(row) > 3 and row[3].text.isdigit() else None,
            )
        )
    return Dati(regions, towns, elected)


DATI_METHOD = (
    "Counted per polling station and published by the Central Election Commission of Latvia on its results site. "
    "Ballots cast are the voters who took part, and valid votes are the valid marks for lists. The site prints the "
    "number of registered voters only for municipalities and constituencies, so no registered voters are stored per "
    "polling station. The valid-envelope counts and each candidate's plus and minus marks are not stored here yet."
)


def build_dati(day: str) -> Package:
    code = DATI[day]
    first = date.fromisoformat(day)
    year = first.year
    saeima = code.startswith("SV")
    english = f"{year} Latvian parliamentary election" if saeima else f"{year} Latvian municipal elections"
    native = f"{year}. gada Saeimas vēlēšanas" if saeima else f"{year}. gada pašvaldību vēlēšanas"
    if saeima:
        native = "15. Saeimas vēlēšanas"
    p = Package(
        f"lv.cvk/{day}",
        SOURCE,
        f"The {english} by polling station from the Central Election Commission",
        depends=["lv.cvk/geo", "lv.cvk/registry"],
    )
    p.external |= {LATVIA, COUNTRY}
    data = read_dati(code)
    for url in (dati_url(code), f"{HOST}/{code}/ieveletie-deputati/"):
        p.uses(raw.cached(SOURCE, url))
    _, region_urls, town_urls = dati_pages(code)
    for url in (*region_urls, *town_urls):
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day)
    where = f"lv/{day}-" + ("general" if saeima else "local")
    p.add(
        "election",
        id=election,
        polity_id=LATVIA,
        kind="general" if saeima else "local",
        date_from=first,
        date_to=first,
        status="held",
    )
    p.name(election, english, "en", "official")
    p.name(election, native, "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    # a contest is a constituency (Saeima) or a council (municipal); each holds the stations that count toward it
    contests: list[tuple[str, str, str, Place, list[tuple[str, Place]]]] = []
    if saeima:
        for slug, region in data.regions.items():
            members = [(f"{r}/{t}", town) for (r, t), town in data.towns.items() if r == slug]
            contests.append((slug, slug, region.name, region, members))
    else:
        for (r, t), town in data.towns.items():
            contests.append((f"{r}/{t}", t, town.name, town, [(f"{r}/{t}", town)]))
    options: dict[tuple[str, int], str] = {}
    sets: dict[str, str] = {}
    abroad = {"arvalstis"}
    for ident, slug, name, place, members in sorted(contests, key=lambda c: c[2]):
        contest = key("contest", day, ident)
        seats = sum(stand.seats for stand in place.stands.values())
        if saeima:
            local = REGION_NAMES[str(next(i for i, s_ in enumerate(REGION_SLUGS.values(), 1) if s_ == slug))]
            title, nativetitle = f"{english}, {local[2]}", f"{native}, {local[1]}"
            district = area("lv.apgabals", str(next(i for i, s_ in enumerate(REGION_SLUGS.values(), 1) if s_ == slug)))
        else:
            title, nativetitle, district = f"{year} {name} council election", f"{name} domes vēlēšanas {year}", None
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=SAEIMA if saeima else None,
            district_area_id=district,
            magnitude=seats or None,
            round_number=1,
            electoral_system_id=SYSTEM_SAEIMA if saeima else SYSTEM_MUNICIPAL,
            default_result_kind="counted",
        )
        p.name(contest, title, "en", "official")
        p.name(contest, nativetitle, "lv", "native", False)
        p.add("slug", path=f"{where}/{slug}", entity_id=contest, is_current=True)
        result_set = sets[ident] = key("result_set", day, ident)
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
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=DATI_METHOD, lang="en"
        )
        for number, stand in sorted(place.stands.items()):
            option = options[(ident, number)] = key("option", day, ident, number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(stand.name),
                ballot_position=number,
                ballot_label=stand.name,
            )
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=stand.votes)
            if stand.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=stand.seats)
        if place.row is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=place.row[1])
            p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=place.row[3])
        for town_slug, town in members:
            for number, label, cast, valid, votes in town.stations:
                unit_code = f"{day}:{town_slug}:{number}"
                unit = key("unit", unit_code)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=LATVIA,
                    scheme_code="lv.iecirknis",
                    code=unit_code,
                    area_relation="unlocated",
                    area_id=None,
                    kind="abroad_mission" if town.slug in abroad else "standard",
                )
                p.name(unit, label, "lv", "official")
                _put(p, result_set, unit, None, CAST, cast)
                _put(p, result_set, unit, None, VALID, valid)
                for number_, option in ((n, o) for (i, n), o in options.items() if i == ident):
                    _put(p, result_set, unit, option, VOTES, votes.get(number_, 0))
    lookup = {
        (ident, stand.name): number
        for ident, slug, name, place, members in contests
        for number, stand in place.stands.items()
    }
    by_name = {name: ident for ident, slug, name, place, members in contests}
    for index, (who, list_name, where_name, position) in enumerate(data.elected):
        ident = by_name[where_name]
        number = lookup[(ident, list_name)]
        option = options[(ident, number)]
        person = key("person", day, ident, who, index)
        p.add("person", id=person)
        p.name(person, who, "lv", "official")
        candidacy = key("candidacy", day, ident, who, index)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="list_member",
            list_position=position,
            ballot_name=who,
        )
        p.add("outcome", result_set_id=sets[ident], option_id=option, candidacy_id=candidacy, kind="elected")
    return p


ARCHIVE = "https://arhivs.cvk.lv/lv"
PV2021 = f"{ARCHIVE}/pv-2021-velesanu-rezultati/"
PV2021_ELECTED = f"{ARCHIVE}/pv-2021-ieveletie-deputati/"


def pv2021_pages() -> list[str]:
    summary = tables(PV2021)
    seen = dict.fromkeys(
        f"{ARCHIVE}/{link.rsplit('/lv/', 1)[-1]}"
        for link in summary.links
        if re.search(r"/lv/pv-2021-velesanu-iecirkni-[^/]+/?$", link)
    )
    return list(seen)


@dataclass
class Page:
    slug: str
    name: str
    stands: dict[int, Stand]
    stations: list[tuple[int, str, str, int, int, dict[int, int]]]
    row: tuple[int, int, int, int]


def _station(text: str) -> tuple[int, str]:
    number, _, name = text.partition(". ")
    return int(number), clean(name)


def read_pv2021() -> tuple[list[Page], list[tuple[str, str, str]]]:
    summary = {
        row[0].text: (_n(row[1]), _n(row[2]), _n(row[4]), _n(row[6]))
        for row in tables(PV2021).tables["t0"][1:]
        if row[0].text != "Kopā"
    }
    pages: list[Page] = []
    for url in pv2021_pages():
        slug = url.rstrip("/").rsplit("/", 1)[-1].removeprefix("pv-2021-velesanu-iecirkni-")
        page = tables(url)
        name = next(
            text
            for link, text in tables(PV2021).anchors
            if link.rstrip("/").endswith(url.rstrip("/").rsplit("/", 1)[-1])
        )
        addresses = {_station(r[0].text)[0]: clean(r[1].text) for r in page.tables["t0"][1:] if r[0].text != "Kopā"}
        stands = {
            int(r[0].text): Stand(clean(r[1].text), _n(r[2]), 0) for r in page.tables["t2"][1:] if r[0].text.isdigit()
        }
        matrix: dict[int, dict[int, int]] = {}
        for r in page.tables["t3"][1:]:
            matrix.setdefault(_station(r[0].text)[0], {})[int(r[1].text)] = _n(r[3])
        stations = []
        for r in page.tables["t1"][1:]:
            if r[0].text == "Kopā":
                continue
            number, label = _station(r[0].text)
            stations.append((number, label, addresses.get(number, ""), _n(r[1]), _n(r[3]), matrix.get(number, {})))
        pages.append(Page(slug, name, stands, stations, summary[name]))
    elected = [
        (clean(r[0].text), clean(r[1].text), clean(r[2].text))
        for r in tables(PV2021_ELECTED).tables["t0"][1:]
        if len(r) >= 3
    ]
    return pages, elected


def build_pv2021() -> Package:
    day = "2021-06-05"
    first = date.fromisoformat(day)
    english, native = "2021 Latvian municipal elections", "2021. gada pašvaldību vēlēšanas"
    p = Package(
        f"lv.cvk/{day}",
        SOURCE,
        f"The {english} by polling station from the Central Election Commission",
        depends=["lv.cvk/geo", "lv.cvk/registry"],
    )
    p.external |= {LATVIA, COUNTRY}
    for url in (PV2021, PV2021_ELECTED, *pv2021_pages()):
        p.uses(raw.cached(SOURCE, url))
    pages, elected = read_pv2021()
    election = key("election", day)
    where = f"lv/{day}-local"
    p.add("election", id=election, polity_id=LATVIA, kind="local", date_from=first, date_to=first, status="held")
    p.name(election, english, "en", "official")
    p.name(election, native, "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    p.add(
        "note",
        id=key("note", election, "scope"),
        entity_id=election,
        kind="context",
        body_md="Riga, Rēzekne, and Varakļāni held their own elections in 2020; they are not part of this vote.",
        lang="en",
    )
    seats = Counter((c, lst) for c, lst, _ in elected)
    options: dict[tuple[str, int], str] = {}
    sets: dict[str, str] = {}
    for page in sorted(pages, key=lambda q: q.name):
        contest = key("contest", day, page.slug)
        total = sum(n for (c, _), n in seats.items() if c == page.name)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            magnitude=total or None,
            round_number=1,
            electoral_system_id=SYSTEM_MUNICIPAL,
            default_result_kind="counted",
        )
        p.name(contest, f"{first.year} {page.name} council election", "en", "official")
        p.name(contest, f"{page.name} domes vēlēšanas {first.year}", "lv", "native", False)
        p.add("slug", path=f"{where}/{page.slug}", entity_id=contest, is_current=True)
        result_set = sets[page.name] = key("result_set", day, page.slug)
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
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=DATI_METHOD, lang="en"
        )
        for number, stand in sorted(page.stands.items()):
            option = options[(page.name, number)] = key("option", day, page.slug, number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(stand.name),
                ballot_position=number,
                ballot_label=stand.name,
            )
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=stand.votes)
            won = seats[(page.name, stand.name)]
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
        p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=page.row[1])
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=page.row[3])
        for number, label, address, cast, valid, votes in page.stations:
            code = f"{day}:{page.slug}:{number}"
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
            p.name(unit, ", ".join(part for part in (label, address) if part), "lv", "official")
            _put(p, result_set, unit, None, CAST, cast)
            _put(p, result_set, unit, None, VALID, valid)
            for list_number in page.stands:
                _put(p, result_set, unit, options[(page.name, list_number)], VOTES, votes.get(list_number, 0))
    by_label = {(page.name, stand.name): n for page in pages for n, stand in page.stands.items()}
    for index, (council, list_name, who) in enumerate(elected):
        option = options[(council, by_label[(council, list_name)])]
        person = key("person", day, council, who, index)
        p.add("person", id=person)
        p.name(person, who, "lv", "official")
        candidacy = key("candidacy", day, council, who, index)
        p.add("candidacy", id=candidacy, person_id=person, option_id=option, role="list_member", ballot_name=who)
        p.add("outcome", result_set_id=sets[council], option_id=option, candidacy_id=candidacy, kind="elected")
    return p


# Elections whose results the commission prints only for constituencies, municipalities, or the whole country.
LEGACY = {
    "1993-06-05": ("saeima", "5-saeimas-velesanas", 5),
    "1995-09-30": ("saeima", "6-saeimas-velesanas", 6),
    "1998-10-03": ("saeima", "7-saeimas-velesanu-rezultati", 7),
    "2002-10-05": ("saeima", "8-saeimas-velesanu-rezultati", 8),
    "2006-10-07": ("saeima", "9-saeimas-velesanu-rezultati", 9),
    "2010-10-02": ("saeima", "10-saeimas-velesanu-rezultati", 10),
    "2011-09-17": ("saeima", "11-saeimas-velesanu-rezultati", 11),
    "2004-06-12": ("ep", "ep-2004-rezultati", 0),
    "2009-06-06": ("ep", "ep-2009-rezultati", 0),
    "2014-05-24": ("ep", "ep-2014-aktivitate-un-rezultati", 0),
}
SEATS_EP = {"2004-06-12": 9, "2009-06-06": 8, "2014-05-24": 8}
LEGACY_REGIONS = ("1", "2", "3", "4", "5")


def legacy_url(day: str) -> str:
    return f"{ARCHIVE}/{LEGACY[day][1]}/"


@dataclass
class Unit:
    code: str
    name: str
    registered: int | None
    cast: int | None
    valid: int | None
    votes: dict[int, int]


@dataclass
class Scope:
    ident: str
    name: str
    region: str | None
    units: list[Unit]
    seats: dict[int, int] = field(default_factory=dict)


@dataclass
class Legacy:
    lists: dict[int, str]
    scopes: list[Scope]
    national: dict[int, tuple[int, int]]  # list number -> votes, seats
    level: str  # the unit scheme: lv.apgabals, lv.pasvaldiba, or lv.valsts


def _cols(row: list[Cell]) -> dict[str, int]:
    return {cell.text.rstrip("."): i for i, cell in enumerate(row)}


def _list_table(rows: list[list[Cell]]) -> tuple[dict[int, str], dict[int, int], dict[int, int]]:
    head = _cols(rows[0])
    number = head["Nr"]
    names: dict[int, str] = {}
    votes: dict[int, int] = {}
    seats: dict[int, int] = {}
    for row in rows[1:]:
        if len(row) > number and row[number].text.isdigit():
            n = int(row[number].text)
            names[n] = clean(row[head["Saraksts"]].text)
            votes[n] = _n(row[head["Balsis"]])
            seats[n] = _n(row[head["Vietas"]]) if head["Vietas"] < len(row) else 0
    return names, votes, seats


def read_legacy(day: str) -> Legacy:
    number = LEGACY[day][2]
    page = tables(legacy_url(day))
    tabs = page.tables
    if day == "1993-06-05":
        rows = [r for r in tabs["t0"][1:] if r[0].text.rstrip(".").isdigit()]
        names = {int(r[0].text.rstrip(".")): clean(r[1].text.strip('"')) for r in rows}
        votes = {int(r[0].text.rstrip(".")): _n(r[3]) for r in rows}
        seats = {int(r[0].text.rstrip(".")): _n(r[5]) for r in rows}
        unit = Unit(f"{day}:valsts", "Latvija", None, None, None, votes)
        return Legacy(
            names,
            [Scope("national", "Latvija", None, [unit], seats)],
            {n: (votes[n], seats[n]) for n in names},
            "lv.valsts",
        )
    if day == "1995-09-30":
        rows = [r for r in tabs["t0"] if len(r) == 9 and r[0].text.rstrip(".").isdigit()]
        names = {int(r[0].text.rstrip(".")): clean(r[1].text) for r in rows}
        scopes = []
        for index, code in enumerate(LEGACY_REGIONS):
            unit = Unit(
                f"{day}:{code}",
                REGION_NAMES[code][0],
                None,
                None,
                None,
                {int(r[0].text.rstrip(".")): _n(r[2 + index]) for r in rows},
            )
            scopes.append(Scope(code, REGION_NAMES[code][0], code, [unit]))
        return Legacy(names, scopes, {int(r[0].text.rstrip(".")): (_n(r[7]), 0) for r in rows}, "lv.apgabals")
    if number in (7, 8, 9):
        names, votes, seats = _list_table(tabs["t0"])
        scopes = []
        for index, code in enumerate(LEGACY_REGIONS):
            _, v, s = _list_table(tabs[f"t{index + 1}"])
            unit = Unit(f"{day}:{code}", REGION_NAMES[code][0], None, None, None, v)
            scopes.append(Scope(code, REGION_NAMES[code][0], code, [unit], s))
        return Legacy(names, scopes, {n: (votes[n], seats[n]) for n in names}, "lv.apgabals")
    if number in (10, 11):
        names = {}
        for r in tabs["t0"][1:]:
            for start in (0, 2):
                if len(r) > start + 1 and r[start].text.rstrip(".").isdigit():
                    names[int(r[start].text.rstrip("."))] = clean(r[start + 1].text)
        head = [c.text.rstrip(".") for c in tabs["t1"][0]]
        scopes = []
        total: dict[int, int] = {}
        for r in tabs["t1"][1:]:
            if r[1].text == "":
                continue
            code = r[0].text
            votes = {int(h): _n(c) for h, c in zip(head, r, strict=True) if h.isdigit()}
            cast = _n(r[head.index("Balsotāju skaits")]) if "Balsotāju skaits" in head else None
            valid = _n(r[head.index("Derīgo zīmju skaits")]) if "Derīgo zīmju skaits" in head else None
            unit = Unit(f"{day}:{code}", REGION_NAMES[code][0], None, cast, valid, votes)
            scopes.append(Scope(code, REGION_NAMES[code][0], code, [unit]))
            for n, v in votes.items():
                total[n] = total.get(n, 0) + v
        return Legacy(names, scopes, {n: (total[n], 0) for n in names}, "lv.apgabals")
    if day == "2014-05-24":
        rows = [r for r in tabs["t1"][1:] if r[0].text.rstrip(".").isdigit()]
        names = {int(r[0].text.rstrip(".")): clean(r[1].text) for r in rows}
        votes = {int(r[0].text.rstrip(".")): _n(r[2]) for r in rows}
        seats = {int(r[0].text.rstrip(".")): _n(r[3]) for r in rows}
        stats = {r[0].text.rstrip(":"): _n(r[1]) for r in tabs["t0"] if len(r) == 2 and r[1].text.isdigit()}
        unit = Unit(f"{day}:valsts", "Latvija", None, stats["Nobalsojušie"], sum(votes.values()), votes)
        return Legacy(
            names,
            [Scope("national", "Latvija", None, [unit], seats)],
            {n: (votes[n], seats[n]) for n in names},
            "lv.valsts",
        )
    names, votes, seats = _list_table(
        [[Cell(c.text.replace("Sar.nr.", "Nr").replace("Sar.Nr.", "Nr"), None, None) for c in r] for r in tabs["t0"]]
    )
    units: list[Unit] = []
    if day == "2004-06-12":
        head = [c.text for c in tabs["t1"][0]]
        for r in tabs["t1"][1:]:
            if r[0].text.startswith("Kopā"):
                continue
            units.append(
                Unit(
                    f"{day}:{len(units) + 1}",
                    clean(r[0].text),
                    _n(r[1]) or None,
                    None,
                    _n(r[-1]),
                    {int(h): _n(c) for h, c in zip(head, r, strict=True) if h.isdigit()},
                )
            )
    else:
        for region, key_ in zip(LEGACY_REGIONS, ("t1", "t2", "t3", "t4", "t5"), strict=True):
            head = [c.text for c in tabs[key_][0]]
            for r in tabs[key_][1:]:
                if r[0].text == "Kopā":
                    continue
                units.append(
                    Unit(
                        f"{day}:{region}:{len(units) + 1}",
                        clean(r[0].text),
                        _n(r[1]),
                        None,
                        _n(r[-1]),
                        {int(h): _n(c) for h, c in zip(head, r, strict=True) if h.isdigit()},
                    )
                )
    return Legacy(
        names,
        [Scope("national", "Latvija", None, units, seats)],
        {n: (votes[n], seats[n]) for n in names},
        "lv.pasvaldiba",
    )


LEGACY_METHOD = (
    "Published by the Central Election Commission of Latvia on its archive site as tables of votes for each list. "
    "The finest unit the commission prints for this election is the {unit}; no polling-station figures are "
    "published. Each list's votes are its valid marks."
)
LEGACY_DAYS = {"1993-06-05": "1993-06-06", "1995-09-30": "1995-10-01"}


def build_legacy(day: str) -> Package:
    kind, _, number = LEGACY[day]
    first = date.fromisoformat(day)
    last = date.fromisoformat(LEGACY_DAYS.get(day, day))
    year = first.year
    if kind == "saeima":
        english, native = f"{year} Latvian parliamentary election", f"{number}. Saeimas vēlēšanas"
    else:
        english, native = f"{year} European Parliament election in Latvia", f"{year}. gada Eiropas Parlamenta vēlēšanas"
    p = Package(
        f"lv.cvk/{day}",
        SOURCE,
        f"The {english} by the finest unit the Central Election Commission publishes",
        depends=["lv.cvk/geo", "lv.cvk/registry"],
    )
    p.external |= {LATVIA, COUNTRY}
    p.uses(raw.cached(SOURCE, legacy_url(day)))
    data = read_legacy(day)
    unit_noun = {"lv.apgabals": "constituency", "lv.pasvaldiba": "municipality", "lv.valsts": "whole country"}[
        data.level
    ]
    method = LEGACY_METHOD.format(unit=unit_noun)
    election = key("election", day)
    where = f"lv/{day}-general"
    p.add("election", id=election, polity_id=LATVIA, kind="general", date_from=first, date_to=last, status="held")
    p.name(election, english, "en", "official")
    p.name(election, native, "lv", "native", False)
    p.add("slug", path=where, entity_id=election, is_current=True)
    for scope in data.scopes:
        ident = scope.ident
        contest = key("contest", day, ident)
        region = scope.region
        total = sum(scope.seats.values()) or SEATS_EP.get(day)
        if region:
            title, nativetitle = f"{english}, {REGION_NAMES[region][2]}", f"{native}, {REGION_NAMES[region][1]}"
            slug, district = REGION_SLUGS[region], area("lv.apgabals", region)
        else:
            title, nativetitle, slug, district = english, native, "national", COUNTRY
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=SAEIMA if kind == "saeima" else None,
            district_area_id=district,
            magnitude=total or (100 if day == "1993-06-05" else None),
            round_number=1,
            electoral_system_id=SYSTEM_SAEIMA if kind == "saeima" else SYSTEM_EP,
            default_result_kind="counted",
        )
        p.name(contest, title, "en", "official")
        p.name(contest, nativetitle, "lv", "native", False)
        p.add("slug", path=f"{where}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, ident)
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
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        options: dict[int, str] = {}
        for n in sorted(data.lists):
            if not any(n in u.votes for u in scope.units):
                continue
            option = options[n] = key("option", day, ident, n)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(data.lists[n]),
                ballot_position=n,
                ballot_label=data.lists[n],
            )
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option,
                measure=VOTES,
                value=sum(u.votes.get(n, 0) for u in scope.units),
            )
            if scope.seats.get(n):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=scope.seats[n])
        for u in scope.units:
            unit = key("unit", u.code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=LATVIA,
                scheme_code=data.level,
                code=u.code,
                area_relation="equals" if data.level != "lv.pasvaldiba" else "unlocated",
                area_id=(area("lv.apgabals", region) if data.level == "lv.apgabals" and region else COUNTRY)
                if data.level != "lv.pasvaldiba"
                else None,
                kind="aggregate" if data.level != "lv.pasvaldiba" else "standard",
            )
            p.name(unit, u.name, "lv", "official")
            _put(p, result_set, unit, None, REGISTERED, u.registered)
            _put(p, result_set, unit, None, CAST, u.cast)
            _put(p, result_set, unit, None, VALID, u.valid)
            for n, option in options.items():
                _put(p, result_set, unit, option, VOTES, u.votes.get(n, 0))
        for measure, getter in ((CAST, "cast"), (VALID, "valid")):
            values = [getattr(u, getter) for u in scope.units]
            if all(v is not None for v in values) and values:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=sum(values))
    return p
