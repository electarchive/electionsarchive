"""Every election on the results sites of Portugal's Ministry of Internal Administration, 2009 to 2026, by parish.

The Ministry's electoral administration publishes one site per election at eleicoes.mai.gov.pt. The sites from 2009
onward read static JSON files, one per territory and organ, down to each parish (freguesia) at home and each consular
post or country abroad. `ea.sources.pt.crawl` keeps each site's files in one zip per election. This module turns them
into one package per election date, a `geo` package for the districts and municipalities, and a `registry` package
for the parties, bodies, offices, and electoral systems.
"""

import functools
import html
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.pt import legacy
from ea.sources.pt.crawl import CANDIDATE_SITES, HOST, Site, bundle_url, store
from ea.sources.pt.legacy import Old

SOURCE = "pt.mai"
PORTUGAL = str(uuid5(package.NAMESPACE, "polity\x1fPT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPRT"))
VOTES, REGISTERED, CAST, INVALID, BLANK = 1, 2, 3, 5, 6

LOCAL = ("LOCAL-500000",)
MADEIRA = ("LOCAL-300000",)
SITES = [
    Site("legislativas2009", "proto", ("AR",)),
    Site("legislativas2011", "proto", ("AR",)),
    Site("legislativas2015", "proto", ("AR",)),
    Site("legislativas2019", "proto", ("AR",)),
    Site("legislativas2022", "ng", ("AR",)),
    Site("legislativas2024", "ng", ("AR",)),
    Site("legislativas2025", "ng", ("AR",)),
    Site("presidenciais2011", "proto", ("PR",)),
    Site("presidenciais2016", "proto", ("PR",)),
    Site("presidenciais2021", "ng", ("PR",)),
    Site("presidenciais2026_1S", "ps", ("PR",)),
    Site("presidenciais2026_2S", "ps", ("PR",)),
    Site("europeias2009", "eu09", ("PE",)),
    Site("europeias2014", "proto", ("EUR",)),
    Site("europeias2019", "proto", ("EUR",)),
    Site("europeias2024", "ng", ("PE",)),
    Site("autarquicas2009", "proto", ("CM", "AM", "AF"), LOCAL),
    Site("autarquicas2013", "proto", ("CM", "AM", "AF"), LOCAL),
    Site("autarquicas2017", "proto", ("CM", "AM", "AF"), LOCAL),
    Site("autarquicas2021", "ng", ("CM", "AM", "AF"), LOCAL),
    Site("autarquicas2025", "ps", ("CM", "AM", "AF")),
    Site("regionais2011", "proto", ("MD",), MADEIRA),
    Site("regionais2015", "proto", ("MD",), MADEIRA),
    Site("regionais2019", "proto", ("RM",), MADEIRA),
    Site("regionais2023", "ng", ("RM",)),
    Site("regionais2024", "ng", ("RA",)),
    Site("regionais-madeira2024", "ng", ("RM",)),
    Site("regionais2025", "ng", ("RM",)),
]
OLD = {
    "legislativas1999": Old("legislativas1999", "v1", ("AR",)),
    "presidenciais2001": Old("presidenciais2001", "v1", ("PR",)),
    "autarquicas2001": Old("autarquicas2001", "v1", ("CM", "AM", "AF")),
    "legislativas2002": Old("legislativas2002", "v1", ("IS",)),
    "europeias2004": Old("europeias2004", "v2", ("IS",)),
    "legislativas2005": Old("legislativas2005", "v2", ("IS",)),
    "Autarquicas2005": Old("Autarquicas2005", "v2", ("CM", "AM", "AF")),
    "Presidenciais2006": Old("Presidenciais2006", "v3", ("IS",)),
    "referendoIVG2007": Old("referendoIVG2007", "v4", ("R",), "0"),
    "regionais2007": Old("regionais2007", "v4", ("R",), "220000"),
    "autarquicas2007": Old("autarquicas2007", "v4", ("R",), "110600"),
}
SITES = [
    Site("legislativas1999", "legacy", ("AR",)),
    Site("presidenciais2001", "legacy", ("PR",)),
    Site("autarquicas2001", "legacy", ("CM", "AM", "AF")),
    Site("legislativas2002", "legacy", ("AR",)),
    Site("europeias2004", "legacy", ("PE",)),
    Site("legislativas2005", "legacy", ("AR",)),
    Site("Autarquicas2005", "legacy", ("CM", "AM", "AF")),
    Site("Presidenciais2006", "legacy", ("PR",)),
    Site("referendoIVG2007", "legacy", ("REF",)),
    Site("regionais2007", "legacy", ("RM",)),
    Site("autarquicas2007", "legacy", ("CM",)),
    *SITES,
]
DAYS = {
    "legislativas1999": date(1999, 10, 10),
    "presidenciais2001": date(2001, 1, 14),
    "autarquicas2001": date(2001, 12, 16),
    "legislativas2002": date(2002, 3, 17),
    "europeias2004": date(2004, 6, 13),
    "legislativas2005": date(2005, 2, 20),
    "Autarquicas2005": date(2005, 10, 9),
    "Presidenciais2006": date(2006, 1, 22),
    "referendoIVG2007": date(2007, 2, 11),
    "regionais2007": date(2007, 5, 6),
    "autarquicas2007": date(2007, 7, 15),
    "legislativas2009": date(2009, 9, 27),
    "legislativas2011": date(2011, 6, 5),
    "legislativas2015": date(2015, 10, 4),
    "legislativas2019": date(2019, 10, 6),
    "legislativas2022": date(2022, 1, 30),
    "legislativas2024": date(2024, 3, 10),
    "legislativas2025": date(2025, 5, 18),
    "presidenciais2011": date(2011, 1, 23),
    "presidenciais2016": date(2016, 1, 24),
    "presidenciais2021": date(2021, 1, 24),
    "presidenciais2026_1S": date(2026, 1, 18),
    "presidenciais2026_2S": date(2026, 2, 8),
    "europeias2009": date(2009, 6, 7),
    "europeias2014": date(2014, 5, 25),
    "europeias2019": date(2019, 5, 26),
    "europeias2024": date(2024, 6, 9),
    "autarquicas2009": date(2009, 10, 11),
    "autarquicas2013": date(2013, 9, 29),
    "autarquicas2017": date(2017, 10, 1),
    "autarquicas2021": date(2021, 9, 26),
    "autarquicas2025": date(2025, 10, 12),
    "regionais2011": date(2011, 10, 9),
    "regionais2015": date(2015, 3, 29),
    "regionais2019": date(2019, 9, 22),
    "regionais2023": date(2023, 9, 24),
    "regionais2024": date(2024, 2, 4),
    "regionais-madeira2024": date(2024, 5, 26),
    "regionais2025": date(2025, 3, 23),
}
ORGANS = {"AR": "AR", "PR": "PR", "PE": "PE", "EUR": "PE", "CM": "CM", "AM": "AM", "AF": "AF", "MD": "RM", "RM": "RM"}
ORGANS["RA"] = "RA"
ORGANS["REF"] = "REF"
ABROAD_CIRCLES = {"europa": "europa", "fora da europa": "fora-da-europa"}
TOTALS = {"GLOBAL-990000", "LOCAL-500000", "FOREIGN-600000"}
GROUP = re.compile(r"^grupo (de )?cidad", re.IGNORECASE)

METHOD = (
    "Counted by parish (freguesia) at home and by consular post or country abroad, as published on the results site "
    "of the Ministry of Internal Administration's electoral administration (eleicoes.mai.gov.pt). Registered voters "
    "are the voters on the roll, ballots cast are the voters who voted, and blank and invalid (nulos) ballots are "
    "printed apart from the votes for each option. Valid votes are not printed and are not stored. The site's "
    "figures are the provisional count of election night; the official tally (apuramento geral) published later in "
    "the Diário da República can differ slightly."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


MAI_SOURCE = key("source", "mai")
ASSEMBLY = key("body", "assembleia-da-republica")
MADEIRA_ASSEMBLY = key("body", "alram")
AZORES_ASSEMBLY = key("body", "alraa")
PRESIDENCY = key("office", "president")
SYSTEM_LIST = key("electoral_system", "dhondt")
SYSTEM_PRESIDENT = key("electoral_system", "president")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")


# ---------------------------------------------------------------------------------------------------------------------
# Fetching


def party_urls(site: Site) -> list[str]:
    base = f"{HOST}/{site.name}"
    if site.family == "proto":
        return [f"{base}/static-data/PARTIES.json"]
    if site.family == "eu09":
        return [f"{base}/data/Parties"]
    if site.family == "ng":
        return [f"{base}/assets/static/parties/parties-{organ}.json" for organ in site.organs]
    if site.family == "legacy":
        return []
    return [f"{base}/assets/static/votingOptions/votingOptions-electionId={_ps_election(site)}.json"]


def _ps_election(site: Site) -> int:
    with zipfile.ZipFile(raw.cached(SOURCE, bundle_url(site)).path) as archive:
        return int(json.loads(archive.read("app-settings.json"))["electionId"])


def fetch() -> None:
    for site in SITES:
        if site.family == "legacy":
            legacy.store(OLD[site.name])
            continue
        store(site)
        for url in party_urls(site):
            try:
                raw.fetch(SOURCE, url, suffix=".json")
            except httpx.HTTPStatusError as failure:
                if failure.response.status_code != 404:
                    raise


def bundle(site: Site) -> raw.Artifact:
    if site.family == "legacy":
        return raw.cached(SOURCE, legacy.bundle_url(OLD[site.name]))
    return raw.cached(SOURCE, bundle_url(site))


def _party_files(site: Site) -> Iterator[raw.Artifact]:
    for url in party_urls(site):
        try:
            yield raw.cached(SOURCE, url)
        except LookupError:
            continue


# ---------------------------------------------------------------------------------------------------------------------
# Reading a site's files into one tree


@dataclass
class Line:
    label: str
    votes: int | None
    seats: int | None
    presidents: int | None
    group: bool = False


@dataclass
class Count:
    registered: int | None
    cast: int | None
    blank: int | None
    invalid: int | None
    seats: int | None
    lines: list[Line]


@dataclass
class Node:
    key: str
    name: str
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    compensation: bool = False
    leaf: bool = False


@dataclass
class Tree:
    site: Site
    nodes: dict[str, Node]
    counts: dict[tuple[str, str], Count]
    top: str
    organs: dict[str, str]  # the site's organ key for each of ours

    def chain(self, key: str) -> list[Node]:
        out = []
        current: str | None = key
        while current is not None:
            node = self.nodes[current]
            out.append(node)
            current = node.parent
        return out

    def leaves(self) -> list[str]:
        return [n.key for n in self.nodes.values() if n.leaf]

    def count(self, key: str, organ: str) -> Count | None:
        return self.counts.get((key, self.organs[organ]))


def _int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return int(str(value))


def _count(current: dict[str, object], groups: set[str]) -> Count:
    lines = []
    for item in _list(current.get("resultsParty")):
        assert isinstance(item, dict)
        label = clean(item.get("acronym"))
        lines.append(
            Line(
                label,
                _int(item.get("votes")),
                _int(item.get("mandates")),
                _int(item.get("presidents")),
                item.get("typeId") == 3 or label in groups,
            )
        )
    return Count(
        _int(current.get("subscribedVoters")),
        _int(current.get("totalVoters") if current.get("totalVoters") is not None else current.get("numberVoters")),
        _int(current.get("blankVotes")),
        _int(current.get("nullVotes")),
        _int(current.get("totalMandates")),
        lines,
    )


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def _decode(data: bytes) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return json.loads(text, strict=False)  # the 2009 party list breaks names across lines


def _list(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _items(artifact: raw.Artifact) -> list[dict[str, object]]:
    """The entries of a party list file, which is a list or a {"data": list}."""
    body = _decode(artifact.path.read_bytes())
    return _list(body.get("data") if isinstance(body, dict) else body)


def group_labels(site: Site) -> set[str]:
    """The labels a site's party lists flag as groups of citizens."""
    out: set[str] = set()
    for artifact in _party_files(site):
        for item in _items(artifact):
            if item.get("groupOfCitizens") or item.get("typeId") == 3:
                out.add(clean(item.get("acronym") or item.get("name")))
    return out


@functools.cache
def tree(site: Site) -> Tree:
    if site.family == "legacy":
        return _legacy_tree(site, OLD[site.name])
    archive = zipfile.ZipFile(raw.cached(SOURCE, bundle_url(site)).path)
    groups = group_labels(site)
    if site.family == "ps":
        return _ps_tree(site, archive, groups)
    nodes: dict[str, Node] = {}
    counts: dict[tuple[str, str], Count] = {}
    named = {"GLOBAL-990000": "Total", "LOCAL-500000": "Território nacional", "FOREIGN-600000": "Estrangeiro"}
    for root in site.roots:
        nodes[root] = Node(root, named.get(root, root))
    for name in archive.namelist():
        body = archive.read(name)
        if site.family == "eu09":
            kind, _, territory = name.removeprefix("data/").partition("/")
            if kind == "LocalTerritoryChilds":
                _children(nodes, territory, _decode(body))
            elif kind == "GlobalAndLocalResults":
                data = _decode(body)
                assert isinstance(data, dict)
                counts[(territory, site.organs[0])] = _eu09(data, groups)
            continue
        stem = name.rsplit("/", 1)[1].removesuffix(".json")
        if "CHILDREN" in stem.upper():
            territory = stem[len("territory-children-") :]
            _children(nodes, territory, _decode(body))
        elif "RESULTS" in stem.upper():
            territory, organ = stem[len("territory-results-") :].rsplit("-", 1)
            data = _decode(body)
            assert isinstance(data, dict)
            current = data.get("currentResults")
            if isinstance(current, dict):
                counts[(territory, organ)] = _count(current, groups)
    for node in nodes.values():
        node.leaf = not node.children and not node.compensation and node.key not in TOTALS
    top = next(root for root in site.roots if any((root, organ) in counts for organ in site.organs))
    if site.name.startswith("regionais") and "LOCAL-300000" in nodes:
        top = "LOCAL-300000"
    if site.name.startswith("regionais") and "LOCAL-400000" in nodes and "LOCAL-300000" not in nodes:
        top = "LOCAL-400000"
    return Tree(site, nodes, counts, top, {ORGANS[o]: o for o in site.organs})


def _children(nodes: dict[str, Node], parent: str, body: object) -> None:
    assert isinstance(body, list)
    if parent not in nodes:
        nodes[parent] = Node(parent, parent)
    for child in body:
        territory = str(child["territoryKey"])
        node = nodes.setdefault(territory, Node(territory, clean(child["name"])))
        node.name = clean(child["name"])
        node.compensation = bool(child.get("compensation"))
        if territory == parent or node.parent == parent:
            continue
        # A district can be listed under both the global total and the national total; keep the narrower parent.
        if node.parent is None or node.parent == "GLOBAL-990000":
            if node.parent is not None:
                nodes[node.parent].children.remove(territory)
            node.parent = parent
            nodes[parent].children.append(territory)


def _eu09(data: dict[str, object], groups: set[str]) -> Count:
    lines = []
    for item in _list(data.get("localStatus")):
        assert isinstance(item, dict)
        label = clean(item.get("acronym"))
        lines.append(Line(label, _int(item.get("votes")), _int(item.get("mandates")), None, label in groups))
    local = data.get("localTerritoryKey")
    return Count(
        _int(data.get("inscribedVoters")),
        _int(data.get("localStatusTotalVoters")),
        _int(data.get("currentBlankVotes")),
        _int(data.get("currentNullVotes")),
        _int(data.get("totalMandates")) if local in ("GLOBAL-990000", None) else None,
        lines,
    )


def _ps_tree(site: Site, archive: zipfile.ZipFile, groups: set[str]) -> Tree:
    election = json.loads(archive.read("app-settings.json"))["electionId"]
    data = json.loads(archive.read(f"assets/static/election/data-electionId={election}.json"))["data"]
    organs = {str(organ["id"]): organ["key"] for organ in data["organs"]}
    tops = data["mainTerritories"]
    top = next((t for t in tops if t["typeId"] == 10), None) or tops[0]
    nodes: dict[str, Node] = {str(top["id"]): Node(str(top["id"]), clean(top["description"]))}
    counts: dict[tuple[str, str], Count] = {}
    for name in archive.namelist():
        found = re.search(r"territoryId=(\d+)(?:-organId=(\d+))?\.json$", name)
        if found is None:
            continue
        territory, organ = found.groups()
        body = json.loads(archive.read(name))
        if "/territory/children-" in name:
            for child in body.get("data") or []:
                id = str(child["id"])
                node = nodes.setdefault(id, Node(id, clean(child["descriptionShort"])))
                node.name = clean(child["descriptionShort"])
                node.leaf = bool(child.get("final"))
                if node.parent is None:
                    node.parent = territory
                    nodes.setdefault(territory, Node(territory, territory)).children.append(id)
        elif "/result/territory-" in name and organ is not None:
            result = body.get("data")
            if isinstance(result, dict) and isinstance(result.get("currentResults"), dict):
                counts[(territory, organs[organ])] = _count(result["currentResults"], groups)
                if territory in nodes:
                    nodes[territory].name = clean(result.get("territoryName")) or nodes[territory].name
    for node in nodes.values():
        node.leaf = node.leaf or (not node.children and node.parent is not None)
    return Tree(site, nodes, counts, str(top["id"]), {ORGANS[o]: o for o in site.organs})


HEADER = re.compile(
    r"(?:CAMARA MUNICIPAL|ASSEMBLEIA MUNICIPAL|ASSEMBLEIA DE FREGUESIA|FREGUESIA) - (.+?) (?:Votos|ASSEMBLEIA)"
)
LEGACY_ROOTS = {"0": "500000", "230000": "500000", "220000": "300000"}
NAMED = re.compile(
    r"""(?:value\s*=\s*["']?(\d{6})["']?[^>]*>([^<]+)|\(\s*["'](\d{6})["']\s*\)\s*ALT\s*=\s*["']([^"']+))""",
    re.IGNORECASE,
)


def _legacy_tree(site: Site, old: Old) -> Tree:
    archive = zipfile.ZipFile(raw.cached(SOURCE, legacy.bundle_url(old)).path)
    pages = {name: archive.read(name) for name in archive.namelist()}
    names: dict[str, str] = {}
    for body in pages.values():
        for first, label, second, alt in NAMED.findall(body.decode("latin-1")):
            code, text = first or second, clean(html.unescape(label or alt))
            if text and not text.lower().startswith("selec"):
                names.setdefault(code, text)

    def key_of(code: str) -> str:
        return f"LOCAL-{LEGACY_ROOTS.get(code, code)}" if code == old.country else f"LOCAL-{code}"

    root = key_of(old.country)
    nodes: dict[str, Node] = {root: Node(root, names.get(old.country, root))}
    codes = {root: old.country}
    counts: dict[tuple[str, str], Count] = {}
    found: set[str] = set()
    for name, body in pages.items():
        found |= legacy.codes(body)
        found |= set(re.findall(r"(\d{6})(?:_\w+)?\.html$", name))
        if (header := HEADER.search(legacy.text(body))) is not None:
            names.setdefault(re.findall(r"(\d{6})", name)[-1], header.group(1))
    level = [old.country]
    while level:
        following = []
        for code in level:
            for child in legacy.children(code, found, old.country):
                if legacy.results_path(old, child, old.types[0]) not in pages or key_of(child) in nodes:
                    continue
                nodes[key_of(child)] = Node(key_of(child), names.get(child, child), key_of(code))
                nodes[key_of(code)].children.append(key_of(child))
                codes[key_of(child)] = child
                following.append(child)
        level = following
    for territory, code in codes.items():
        for kind in old.types:
            body = pages.get(legacy.results_path(old, code, kind))
            if body is None:
                continue
            figures, lines, _ = legacy.read(body, old.layout)
            if not lines:
                continue
            counts[(territory, kind)] = Count(
                figures.get("registered"),
                figures.get("cast"),
                figures.get("blank"),
                figures.get("invalid"),
                sum(seats or 0 for _, _, seats in lines) or None,
                [Line(label, votes, seats, None, bool(GROUP.match(label))) for label, votes, seats in lines],
            )
    for node in nodes.values():
        node.leaf = not node.children and node.key != root
    organs = dict(zip(site.organs, old.types, strict=True))
    return Tree(site, nodes, counts, root, organs)


# ---------------------------------------------------------------------------------------------------------------------
# Places


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.lower()).split())


def district_code(territory: str) -> str:
    digits = territory.split("-")[1][:2]
    return {"19": "40", "20": "40", "21": "40", "22": "30"}.get(digits) or {"3": "30", "4": "40"}.get(digits[0], digits)


def is_local(territory: str) -> bool:
    return territory.startswith("LOCAL-")


@functools.cache
def places() -> tuple[dict[str, str], dict[str, tuple[str, str]], dict[tuple[str, str], str]]:
    """Districts by code, municipalities by code with their district, and municipality codes by folded names."""
    districts: dict[str, str] = {}
    towns: dict[str, tuple[str, str]] = {}
    for site in SITES:
        if site.family not in ("proto", "ng"):
            continue
        t = tree(site)
        for node in t.nodes.values():
            code = node.key.split("-")[1] if is_local(node.key) else ""
            if not code or node.compensation or code in ("500000", "990000"):
                continue
            if code.endswith("0000") and code[:2] not in ("41", "42", "43", "44", "45", "46", "47", "48", "49", "51"):
                districts[district_code(node.key)] = node.name
            elif code.endswith("00") and not code.endswith("0000"):
                towns[code[:4]] = (re.sub(r"\s*\(R\.\s?A\.\s?[AM]\.?\)$", "", node.name), district_code(node.key))
    by_name = {(d, fold(name)): code for code, (name, d) in towns.items()}
    return districts, towns, by_name


def district_area(code: str) -> str:
    return key("area", "pt.distrito", code)


def town_area(code: str) -> str:
    return key("area", "pt.concelho", code)


def circle_area(code: str) -> str:
    return key("area", "pt.circulo", code)


ISLANDS = {
    "41": "Santa Maria",
    "42": "São Miguel",
    "43": "Terceira",
    "44": "Graciosa",
    "45": "São Jorge",
    "46": "Pico",
    "47": "Faial",
    "48": "Flores",
    "49": "Corvo",
}


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Portugal's districts and autonomous regions, its municipalities, and its electoral circles abroad and in the "
        "Azores, as the Ministry of Internal Administration's results sites list them",
    )
    p.external.add(PORTUGAL)
    p.external.add(COUNTRY)
    for code, rank, kind, noun in (
        ("pt.distrito", 1, "administrative", "district or autonomous region"),
        ("pt.concelho", 2, "administrative", "municipality"),
        ("pt.circulo", 1, "electoral_district", "electoral circle"),
        ("pt.freguesia", 3, "administrative", "parish"),
        ("pt.posto", 3, "precinct", "consular post"),
    ):
        p.add("scheme", code=code, polity_id=PORTUGAL, kind=kind, rank=rank, noun=noun, code_system=None)
    districts, towns, _ = places()
    for code, name in sorted(districts.items()):
        id = district_area(code)
        p.add("area", id=id, scheme_code="pt.distrito")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "pt", "official")
    for code, (name, district) in sorted(towns.items()):
        id = town_area(code)
        p.add("area", id=id, scheme_code="pt.concelho")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=district_area(district))
        p.name(id, name, "pt", "official")
    for code, name, parent in (
        ("europa", "Europa", None),
        ("fora-da-europa", "Fora da Europa", None),
        *((island, name, district_area("40")) for island, name in ISLANDS.items()),
    ):
        id = circle_area(code)
        p.add("area", id=id, scheme_code="pt.circulo")
        p.add("area_code", area_id=id, code=code)
        if parent is not None:
            p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, name, "pt", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Registry: parties, bodies, offices, systems


def municipality(t: Tree, leaf: str) -> str | None:
    """The municipality code of a parish at home."""
    chain = t.chain(leaf)
    if t.site.family in ("proto", "ng", "eu09"):
        return leaf.split("-")[1][:4] if is_local(leaf) else None
    districts, _, by_name = places()
    if t.site.family == "legacy":
        district, town = district_code(leaf), chain[1].name
    else:
        names = [fold(node.name) for node in chain]
        if "portugal" not in names:
            return None
        if names.index("portugal") != 3:
            raise LookupError(f"{t.site.name}: {leaf} is not a parish under a municipality and a district")
        codes = {fold(name): code for code, name in districts.items()}
        district, town = codes[fold(chain[2].name)], chain[1].name
    found = by_name.get((district, fold(TOWN_NAMES.get(fold(town), town))))
    if found is None:
        raise LookupError(f"{t.site.name}: no municipality code for {district} / {town}")
    return found


# Abbreviated or older municipality names on the 1999 to 2007 sites, by folded name, and their current names.
TOWN_NAMES = {
    "freixo espada a cinta": "Freixo de Espada à Cinta",
    "vila real sto antonio": "Vila Real de Santo António",
    "fig castelo rodrigo": "Figueira de Castelo Rodrigo",
    "sta marta de penaguiao": "Santa Marta de Penaguião",
    "praia da vitoria": "Vila da Praia da Vitória",
}


def local_bodies() -> dict[str, tuple[str, str]]:
    """Every municipal and parish body the local elections hold, by id: (English name, Portuguese name)."""
    out: dict[str, tuple[str, str]] = {}
    _, towns, _ = places()
    for site in SITES:
        if not site.name.lower().startswith("autarquicas"):
            continue
        t = tree(site)
        for leaf in t.leaves():
            code = municipality(t, leaf)
            if code is None:
                continue
            town = towns[code][0]
            out[council(code)] = (f"{town} Municipal Chamber", f"Câmara Municipal ({town})")
            out[assembly(code)] = (f"{town} Municipal Assembly", f"Assembleia Municipal ({town})")
            parish = t.nodes[leaf].name
            out[parish_assembly(code, parish)] = (
                f"{parish} Parish Assembly ({town})",
                f"Assembleia de Freguesia ({parish}, {town})",
            )
    return out


def council(code: str) -> str:
    return key("body", "cm", code)


def assembly(code: str) -> str:
    return key("body", "am", code)


def parish_assembly(code: str, parish: str) -> str:
    return key("body", "af", code, fold(parish))


def party(label: str) -> str:
    return key("party", label)


def is_bloc(label: str) -> bool:
    return len([part for part in label.split(".") if len(part) >= 2]) >= 2


@functools.cache
def party_names() -> dict[str, tuple[str | None, bool]]:
    """Every party and coalition label the sites print, with its full name and whether it is a coalition. Groups of
    citizens and presidential candidates are left out."""
    out: dict[str, tuple[str | None, bool]] = {}
    for site in SITES:
        if site.organs == ("PR",):
            continue
        for artifact in _party_files(site):
            for item in _items(artifact):
                if item.get("groupOfCitizens") or item.get("typeId") == 3:
                    continue
                label = clean(item.get("acronym") or item.get("name"))
                name = clean(item.get("name") if "acronym" in item else item.get("description"))
                if not label:
                    continue
                known = out.get(label)
                out[label] = (
                    name if name and name != "-" else (known[0] if known else None),
                    bool(item.get("coalition")) or item.get("typeId") == 2 or is_bloc(label),
                )
        t = tree(site)
        for organ in t.organs:
            count = t.count(t.top, organ)
            for line in count.lines if count else []:
                if line.group or GROUP.match(line.label) or not line.label:
                    continue
                out.setdefault(line.label, (None, is_bloc(line.label)))
    return out


SYSTEMS = (
    (
        SYSTEM_LIST,
        {"formula": "dhondt"},
        "Closed party lists; seats in each electoral circle go to the lists by the D'Hondt method, with no threshold.",
    ),
    (
        SYSTEM_PRESIDENT,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate with more than half of the valid votes is elected; otherwise the two leading candidates go "
            "to a second round."
        ),
    ),
    (SYSTEM_REFERENDUM, {"formula": "referendum"}, "A question put to all voters, answered yes or no."),
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Portuguese parties and coalitions, the Assembly of the Republic, the regional assemblies, the municipal and "
        "parish bodies, the presidency, and their electoral systems, from the Ministry of Internal Administration's "
        "results sites",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(PORTUGAL)
    p.add(
        "source",
        id=MAI_SOURCE,
        kind="official_body",
        publisher="Ministério da Administração Interna, Secretaria-Geral, Administração Eleitoral",
        title="Election results sites (eleicoes.mai.gov.pt)",
        url=f"{HOST}/",
        license="Official publication; reuse terms are not stated",
        attribution="Ministério da Administração Interna, Administração Eleitoral",
    )
    for site in SITES:
        p.artifact(bundle(site), MAI_SOURCE)
        for artifact in _party_files(site):
            p.artifact(artifact, MAI_SOURCE)
    for id, english, portuguese in (
        (ASSEMBLY, "Assembly of the Republic", "Assembleia da República"),
        (MADEIRA_ASSEMBLY, "Legislative Assembly of Madeira", "Assembleia Legislativa da Região Autónoma da Madeira"),
        (AZORES_ASSEMBLY, "Legislative Assembly of the Azores", "Assembleia Legislativa da Região Autónoma dos Açores"),
    ):
        p.add("body", id=id, polity_id=PORTUGAL, kind="unicameral")
        p.name(id, english, "en", "official")
        p.name(id, portuguese, "pt", "native", False)
    for id, (english, portuguese) in sorted(local_bodies().items(), key=lambda item: item[1]):
        p.add("body", id=id, polity_id=PORTUGAL, kind="council")
        p.name(id, english, "en", "official")
        p.name(id, portuguese, "pt", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=PORTUGAL, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Portuguese Republic", "en", "official")
    p.name(PRESIDENCY, "Presidente da República Portuguesa", "pt", "native", False)
    for id, rules, text in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=PORTUGAL, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for label, (name, bloc) in sorted(party_names().items()):
        id = party(label)
        p.add("party", id=id, kind="bloc" if bloc else "party", polity_id=PORTUGAL)
        p.add("external_id", entity_id=id, scheme="pt.mai.party", value=label)
        if name:
            p.name(id, name, "pt", "official")
            p.name(id, label, "pt", "abbreviation", False)
        else:
            p.name(id, label, "pt", "official")
    people: dict[str, tuple[str, str]] = {}
    for site in SITES:
        if site.organs != ("PR",):
            continue
        for label, full in candidates(site).items():
            known_name, known_label = people.get(fold(label), (label, label))
            better = full if full != label or known_name == known_label else known_name
            people[fold(label)] = (better, known_label if not known_label.isupper() else label)
    for _, (name, label) in sorted(people.items()):
        id = person(label)
        p.add("person", id=id)
        p.name(id, name, "pt", "official")
        if label != name:
            p.name(id, label, "pt", "ballot", False)
    return p


def candidates(site: Site) -> dict[str, str]:
    """A presidential site's candidates: the full name for each ballot label."""
    names: dict[str, str] = {}
    for artifact in _party_files(site):
        for item in _items(artifact):
            label = clean(item.get("acronym") or item.get("name"))
            full = clean(item.get("name") if "acronym" in item else item.get("description"))
            names[label] = full or label
    t = tree(site)
    count = t.count(t.top, "PR")
    for line in count.lines if count else []:
        names.setdefault(line.label, line.label)
    return {label: names[label] for label in (line.label for line in count.lines)} if count else {}


def person(name: str) -> str:
    return key("person", fold(name))


@dataclass
class Slate:
    """One list's candidates in one territory: the head candidates of a council, or the effective and alternate
    candidates of a list, in ballot order."""

    effective: list[str]
    alternates: list[str]
    mandates: int
    elected: list[str] | None  # the names a site's elected-only pages print, when they filter
    heads: int  # presidents elected, for a municipal council


PROTO_CANDIDATES = re.compile(r"PARTIES-CANDIDATES-([A-Z]+)-.*PAGE-\d+-ELECTED-(TRUE|FALSE)\.json$")
NG_CANDIDATES = re.compile(r"parties-candidates-([A-Z]+)(?:-.+)?-\d+-(true|false)\.json$")


def _names(value: object) -> list[str]:
    return [clean(name) for name in value if clean(name)] if isinstance(value, list) else []


@functools.cache
def slates(site: Site) -> dict[tuple[str, str], dict[str, Slate]]:
    """The candidate lists of a site, by organ and territory key, then by party label."""
    if site.name not in CANDIDATE_SITES:
        return {}
    archive = zipfile.ZipFile(raw.cached(SOURCE, bundle_url(site)).path)
    if site.family == "ps":
        return _ps_slates(site, archive)
    out: dict[tuple[str, str], dict[str, Slate]] = {}
    elected: dict[tuple[str, str, str], list[str]] = {}
    pattern = PROTO_CANDIDATES if site.family == "proto" else NG_CANDIDATES
    for name in sorted(archive.namelist()):
        found = pattern.search(name)
        if found is None:
            continue
        site_organ, flag = found.group(1), found.group(2).lower() == "true"
        organ = ORGANS[site_organ]
        body = _decode(archive.read(name))
        for entry in _list(body.get("electionCandidates") if isinstance(body, dict) else None):
            territory = str(entry["territoryKey"])
            for item in _list(entry.get("candidates")):
                label = clean(item["party"])
                if flag:
                    elected[(organ, territory, label)] = _names(item.get("effectiveCandidates"))
                    continue
                out.setdefault((organ, territory), {})[label] = Slate(
                    _names(item.get("effectiveCandidates")),
                    _names(item.get("alternateCandidates")),
                    _int(item.get("mandates")) or 0,
                    None,
                    _int(item.get("presidents")) or 0,
                )
    for (organ, territory, label), names in elected.items():
        slate = out.get((organ, territory), {}).get(label)
        if slate is not None and len(names) == slate.mandates and names != slate.effective:
            slate.elected = names
    return out


def _ps_slates(site: Site, archive: zipfile.ZipFile) -> dict[tuple[str, str], dict[str, Slate]]:
    """The 2025 local sites list each voting option across every municipality; only the council head is published."""
    out: dict[tuple[str, str], dict[str, Slate]] = {}
    for name in sorted(archive.namelist()):
        if "/candidate/candidates-" not in name or "candidatesElected=false" not in name:
            continue
        body = json.loads(archive.read(name)).get("data") or {}
        for region in _list(body.get("territories")):
            for town in _list(region.get("territories")):
                for option in _list(town.get("votingOptions")):
                    voting = option.get("votingOption")
                    people = [c for c in _list(option.get("candidates")) if c.get("type") == "Efetivo"]
                    if not isinstance(voting, dict) or not people:
                        continue
                    label = clean(voting.get("name"))
                    president = any(c.get("state") == "President" for c in people)
                    out.setdefault(("CM", str(town["id"])), {})[label] = Slate(
                        [clean(c["name"]) for c in sorted(people, key=lambda c: int(str(c["position"])))],
                        [],
                        0,
                        None,
                        1 if president else 0,
                    )
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Elections


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{DAYS[site.name]}" for site in SITES)]


def parse() -> None:
    geo().write()
    registry().write()
    for site in SITES:
        election(site).write()


def emit() -> None:
    package.run("load", packages())


def compare() -> None:
    """Print, for each site, how the stored parish and post rows add up against the site's own top-level totals."""
    for site in SITES:
        t = tree(site)
        for organ in t.organs:
            top = t.count(t.top, organ)
            if top is None:
                continue
            summed: Counter[str] = Counter()
            for leaf in units_of(t, organ):
                count = t.count(leaf, organ)
                if count is None:
                    continue
                summed["registered"] += count.registered or 0
                summed["cast"] += count.cast or 0
                summed["blank"] += count.blank or 0
                summed["invalid"] += count.invalid or 0
                summed["votes"] += sum(line.votes or 0 for line in count.lines)
            printed = {
                "registered": top.registered,
                "cast": top.cast,
                "blank": top.blank,
                "invalid": top.invalid,
                "votes": sum(line.votes or 0 for line in top.lines),
            }
            off = {k: (summed[k], v) for k, v in printed.items() if v is not None and summed[k] != v}
            print(f"{site.name} {organ}: {'match' if not off else off}", flush=True)


def units_of(t: Tree, organ: str) -> list[str]:
    """The leaves that count for an organ: every leaf, or for a regional election only the region's."""
    leaves = [leaf for leaf in t.leaves() if t.count(leaf, organ) is not None]
    if organ in ("RM", "RA"):
        top = t.top
        leaves = [leaf for leaf in leaves if any(n.key == top for n in t.chain(leaf))]
    return leaves


def contest_node(t: Tree, organ: str, leaf: str) -> str | None:
    """The node whose territory is the contest a leaf votes in."""
    chain = t.chain(leaf)
    if organ in ("PR", "PE", "REF"):
        return t.top
    if organ == "RM":
        return t.top
    if organ == "AF":
        return leaf
    if organ in ("CM", "AM"):
        return chain[1].key if len(chain) > 1 else None
    if organ == "AR":
        for node in chain:
            if fold(node.name) in ABROAD_CIRCLES:
                return node.key
            if node.parent == "LOCAL-500000":
                return node.key
        return None
    if organ == "RA":
        for node in chain:
            if re.fullmatch(r"LOCAL-4[1-9]0000", node.key):
                return node.key
        return None
    raise ValueError(organ)


@dataclass
class Built:
    values: list[tuple[str, str, str | None, int, int]] = field(default_factory=list)
    units: dict[str, str] = field(default_factory=dict)


ELECTION_TEXT = {
    "legislativas": (
        "general",
        "{year} Portuguese legislative election",
        "Eleições legislativas de {year}",
        "general",
    ),
    "presidenciais": (
        "general",
        "{year} Portuguese presidential election",
        "Eleição presidencial de {year}",
        "presidential",
    ),
    "europeias": (
        "general",
        "{year} European Parliament election in Portugal",
        "Eleições europeias de {year}",
        "european",
    ),
    "autarquicas": ("local", "{year} Portuguese local elections", "Eleições autárquicas de {year}", "local"),
    "referendoivg": (
        "referendum",
        "{year} Portuguese abortion referendum",
        "Referendo sobre a interrupção voluntária da gravidez de {year}",
        "referendum",
    ),
}


def election(site: Site) -> Package:
    day = DAYS[site.name]
    t = tree(site)
    runoff = site.name.endswith("_2S")
    first = next((s for s in SITES if s.name == site.name.replace("_2S", "_1S")), None) if runoff else None
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Portuguese election of {day} by parish and consular post, from the Ministry of Internal "
        "Administration's results site",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *([f"{SOURCE}/{DAYS[first.name]}"] if first else [])],
    )
    p.external.add(PORTUGAL)
    p.uses(bundle(site))
    for artifact in _party_files(site):
        p.uses(artifact)
    family = re.sub(r"[^a-z]", "", site.name.lower().split("2")[0].replace("regionais-madeira", "regionais"))
    if family == "regionais":
        azores = "RA" in t.organs
        kind, english, portuguese, tail = (
            "general",
            "{year} Azores regional election" if azores else "{year} Madeira regional election",
            (
                "Eleições legislativas regionais dos Açores de {year}"
                if azores
                else "Eleições legislativas regionais da Madeira de {year}"
            ),
            "azores-regional" if azores else "madeira-regional",
        )
    elif site.name == "autarquicas2007":
        kind, english, portuguese, tail = (
            "by_election",
            "{year} Lisbon municipal by-election",
            "Eleição intercalar para a Câmara Municipal de Lisboa de {year}",
            "lisbon-by-election",
        )
    else:
        kind, english, portuguese, tail = ELECTION_TEXT[family]
    if runoff:
        kind, tail = "runoff", f"{tail}-runoff"
        english, portuguese = f"{english}, second round", f"{portuguese}, segunda volta"
    election_id = key("election", site.name)
    path = f"pt/{day}-{tail}"
    p.add("election", id=election_id, polity_id=PORTUGAL, kind=kind, date_from=day, date_to=day, status="held")
    p.name(election_id, english.format(year=day.year), "en", "official")
    p.name(election_id, portuguese.format(year=day.year), "pt", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    built = Built()
    slugs: set[str] = {path}
    _, towns, _ = places()
    known = party_names()
    people = candidates(site) if t.organs.keys() == {"PR"} else {}
    for organ in t.organs:
        groups: dict[str, list[str]] = defaultdict(list)
        for leaf in units_of(t, organ):
            node = contest_node(t, organ, leaf)
            if node is not None:
                groups[node].append(leaf)
        contests = [(node, leaves) for node, leaves in groups.items()]
        if organ == "RA":
            compensation = next((n.key for n in t.nodes.values() if n.compensation), None)
            if compensation is not None:
                contests.append((compensation, units_of(t, organ)))
        for node, leaves in sorted(contests, key=lambda item: (t.nodes[item[0]].name, item[0])):
            _contest(p, t, site, organ, node, leaves, election_id, path, slugs, built, towns, known, people, first)
    _values(p, built)
    _turnout(p, site, built)
    if family == "autarquicas" and slates(site):
        p.accept(
            "outcomes.list_members",
            "the results site names only the head of each municipal council list, so one elected candidate stands "
            "against the several council seats the list won",
        )
    return p


# Contests whose printed totals count parishes the site does not list, by site and contest territory.
GAPS = {
    ("legislativas2002", "LOCAL-090000"): (
        "The 2002 results site lists three of Manteigas's four parishes; Vale de Amoreira has no page, while the "
        "municipal, district, and national totals count it. The parish rows here therefore add up to less than the "
        "printed totals of the Guarda circle."
    ),
    ("legislativas2005", "LOCAL-120000"): (
        "The 2005 results site's page for the parish of Arez (Nisa) prints no row for PS, while the municipal, "
        "district, and national totals count 139 PS votes there. The parish rows here therefore add up to 139 fewer "
        "PS votes than the printed totals of the Portalegre circle."
    ),
    ("europeias2019", "GLOBAL-990000"): (
        "On the 2019 results site, the total of the Évora district counts 15,735 ballots (14,536 votes for the lists, "
        "976 blank, and 223 invalid) that none of its municipality or parish rows carries; every other district "
        "adds up. The parish rows here therefore add up to that much less than the printed national totals."
    ),
}


MOBILITY = (
    "Voters could vote at any polling station in the country that day (voto em mobilidade), so a parish can count "
    "more ballots than voters on its own roll."
)


def _turnout(p: Package, site: Site, built: Built) -> None:
    """Accept and disclose units that print more ballots cast than registered voters."""
    registered = {(s, u): v for s, u, o, m, v in built.values if o is None and m == REGISTERED}
    over: Counter[str] = Counter()
    for s, u, o, m, v in built.values:
        if o is None and m == CAST and (s, u) in registered and v > registered[(s, u)]:
            over[s] += 1
    if not over:
        return
    why = MOBILITY if site.name == "europeias2024" else "The figures are stored as printed."
    p.accept(
        "accounting.turnout",
        f"the results site prints more ballots cast than registered voters in {over.total()} units; "
        + (
            "in 2024 voters could vote at any polling station (voto em mobilidade)"
            if site.name == "europeias2024"
            else "stored as printed"
        ),
    )
    for result_set, n in over.items():
        p.add(
            "note",
            id=key("note", result_set, "turnout"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {n} unit{'s' if n > 1 else ''}, more ballots were cast than voters were on the roll. {why}",
            lang="en",
        )


def _unit(p: Package, t: Tree, day: date, leaf: str, built: Built, towns: dict[str, tuple[str, str]]) -> str:
    if leaf in built.units:
        return built.units[leaf]
    unit = built.units[leaf] = key("unit", day, leaf)
    code = municipality(t, leaf)
    chain = t.chain(leaf)
    abroad = code is None
    if abroad:
        relation, area, kind, scheme = "unlocated", None, "abroad_mission", "pt.posto"
    else:
        relation, area, kind, scheme = "within", town_area(code), "standard", "pt.freguesia"
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=PORTUGAL,
        scheme_code=scheme,
        code=f"{day}:{leaf}",
        area_relation=relation,
        area_id=area,
        kind=kind,
    )
    where = towns[code][0] if code else (chain[1].name if len(chain) > 2 else "")
    name = t.nodes[leaf].name
    p.name(unit, f"{name} ({where})" if where and fold(where) != fold(name) else name, "pt", "official")
    return unit


def _slug(path: str, text: str, slugs: set[str], extra: str = "") -> str:
    base = f"{path}/{changeset.slug(text) or 'contest'}"
    candidate = base
    if candidate in slugs and extra:
        candidate = f"{path}/{changeset.slug(f'{text} {extra}')}"
    n = 2
    while candidate in slugs:
        candidate = f"{base}-{n}"
        n += 1
    slugs.add(candidate)
    return candidate


def _labels(lines: list[Line]) -> list[tuple[str, int]]:
    seen: Counter[str] = Counter()
    out = []
    for line in lines:
        out.append((line.label, seen[line.label]))
        seen[line.label] += 1
    return out


def _contest(
    p: Package,
    t: Tree,
    site: Site,
    organ: str,
    node: str,
    leaves: list[str],
    election_id: str,
    path: str,
    slugs: set[str],
    built: Built,
    towns: dict[str, tuple[str, str]],
    known: dict[str, tuple[str | None, bool]],
    people: dict[str, str],
    first: Site | None,
) -> None:
    day = DAYS[site.name]
    declared = t.count(node, organ)
    if declared is None and organ != "AF":
        raise LookupError(f"{site.name}: no {organ} results for contest {node}")
    counts = {leaf: t.count(leaf, organ) for leaf in leaves}
    if not any(line.votes for count in counts.values() if count for line in count.lines):
        return
    contest = key("contest", site.name, organ, node)
    name = t.nodes[node].name
    town = municipality(t, leaves[0]) if organ in ("CM", "AM", "AF") else None
    town_name = towns[town][0] if town else ""
    fields: dict[str, object] = {"round_number": 1, "default_result_kind": "counted"}
    magnitude = declared.seats if declared and declared.seats else None
    if organ == "REF":
        english, portuguese, slug_text = (
            "Referendum on the decriminalization of abortion",
            "Referendo sobre a despenalização da interrupção voluntária da gravidez",
            "question",
        )
        fields.update(kind="ballot_question", electoral_system_id=SYSTEM_REFERENDUM)
        district = None
    elif organ == "PR":
        english, portuguese, slug_text = "President", "Presidente da República", "president"
        fields.update(office_id=PRESIDENCY, magnitude=1, electoral_system_id=SYSTEM_PRESIDENT, kind="office")
        if first is not None:
            fields.update(round_number=2, previous_round_id=key("contest", first.name, organ, tree(first).top))
        district = None
    else:
        fields.update(kind="list_allocation", magnitude=magnitude, electoral_system_id=SYSTEM_LIST)
        if organ == "AR":
            english = portuguese = slug_text = name
            fields["body_id"] = ASSEMBLY
            abroad = fold(name) in ABROAD_CIRCLES
            district = circle_area(ABROAD_CIRCLES[fold(name)]) if abroad else district_area(district_code(node))
        elif organ == "PE":
            english, portuguese, slug_text = "European Parliament", "Parlamento Europeu", "european-parliament"
            district = None
        elif organ == "RM":
            english, portuguese, slug_text = "Madeira", "Madeira", "madeira"
            fields["body_id"] = MADEIRA_ASSEMBLY
            district = district_area("30")
        elif organ == "RA":
            compensation = t.nodes[node].compensation
            english = portuguese = slug_text = name
            fields["body_id"] = AZORES_ASSEMBLY
            district = district_area("40") if compensation else circle_area(node.split("-")[1][:2])
        elif organ in ("CM", "AM"):
            assert town is not None
            label = "Municipal Chamber" if organ == "CM" else "Municipal Assembly"
            english = f"{town_name} {label}"
            portuguese = f"{'Câmara Municipal' if organ == 'CM' else 'Assembleia Municipal'} ({town_name})"
            slug_text = f"{town_name} {'municipal-chamber' if organ == 'CM' else 'municipal-assembly'}"
            fields["body_id"] = council(town) if organ == "CM" else assembly(town)
            district = town_area(town)
        else:
            assert town is not None
            english = f"{name} Parish Assembly ({town_name})"
            portuguese = f"Assembleia de Freguesia ({name}, {town_name})"
            slug_text = f"{town_name} {name}"
            fields["body_id"] = parish_assembly(town, name)
            district = town_area(town)
            if magnitude is None:
                fields["magnitude"] = None
        fields["district_area_id"] = district
    kind = fields.pop("kind")
    p.add("contest", id=contest, election_id=election_id, kind=kind, **fields)
    if kind == "ballot_question":
        p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, english, "en", "official")
    if portuguese != english:
        p.name(contest, portuguese, "pt", "native", False)
    extra = places()[0].get(towns[town][1], "") if town else ""
    p.add("slug", path=_slug(path, slug_text, slugs, extra), entity_id=contest, is_current=True)
    result_set = key("result_set", site.name, organ, node)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=MAI_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    order: list[tuple[str, int]] = []
    for count in [declared, *counts.values()]:
        for label in _labels(count.lines if count else []):
            if label not in order:
                order.append(label)
    options: dict[tuple[str, int], str] = {}
    for position, (label, n) in enumerate(order, 1):
        option = options[(label, n)] = key("option", site.name, organ, node, label, n)
        if organ == "REF":
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        elif organ == "PR":
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=label,
            )
            p.add(
                "candidacy",
                id=key("candidacy", site.name, label),
                person_id=person(label),
                option_id=option,
                role="head",
                ballot_name=label,
                is_independent=True,
            )
        else:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(label) if label in known else None,
                ballot_position=position,
                ballot_label=label,
            )
    for leaf, count in counts.items():
        if count is None:
            continue
        unit = _unit(p, t, day, leaf, built, towns)
        for measure, value in (
            (REGISTERED, count.registered),
            (CAST, count.cast),
            (BLANK, count.blank),
            (INVALID, count.invalid),
        ):
            if value is not None:
                built.values.append((result_set, unit, None, measure, value))
        for (label, n), line in zip(_labels(count.lines), count.lines, strict=True):
            if line.votes is not None:
                built.values.append((result_set, unit, options[(label, n)], VOTES, line.votes))
    if declared is not None and node not in counts and not t.nodes[node].compensation:
        for measure, value in (
            (REGISTERED, declared.registered),
            (CAST, declared.cast),
            (BLANK, declared.blank),
            (INVALID, declared.invalid),
        ):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for (label, n), line in zip(_labels(declared.lines), declared.lines, strict=True):
            if line.votes is not None:
                p.add(
                    "declared", result_set_id=result_set, option_id=options[(label, n)], measure=VOTES, value=line.votes
                )
    gap = GAPS.get((site.name, node))
    if gap is not None:
        p.accept("accounting.declared", gap)
        p.add("note", id=key("note", result_set, "gap"), entity_id=result_set, kind="caveat", body_md=gap, lang="en")
    if declared is not None:
        _outcomes(p, organ, result_set, declared, options)
    _slates(p, site, organ, node, result_set, options)


def _slates(
    p: Package, site: Site, organ: str, node: str, result_set: str, options: dict[tuple[str, int], str]
) -> None:
    """Add the people on each list and, from the lists' mandates, who was elected."""
    here = slates(site).get((organ, node))
    if not here:
        return
    for (label, n), option in options.items():
        slate = here.get(label)
        if n != 0 or slate is None:
            continue
        council = organ == "CM"
        elected = set(slate.elected if slate.elected is not None else slate.effective[: slate.mandates])
        if council:
            elected = set(slate.effective[: slate.heads])
        positions = [(name, "head" if council else "list_member", i) for i, name in enumerate(slate.effective, 1)]
        positions += [(name, "substitute", i) for i, name in enumerate(slate.alternates, len(slate.effective) + 1)]
        for name, role, position in positions:
            tag = (site.name, organ, node, label, position)
            who = key("person", *tag)
            p.add("person", id=who)
            p.name(who, name, "pt", "official")
            candidacy = key("candidacy", *tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=who,
                option_id=option,
                role=role,
                list_position=position,
                ballot_name=name,
            )
            if role != "substitute" and name in elected:
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")


def _outcomes(p: Package, organ: str, result_set: str, declared: Count, options: dict[tuple[str, int], str]) -> None:
    labelled = list(zip(_labels(declared.lines), declared.lines, strict=True))
    if organ == "REF":
        return
    if organ == "PR":
        total = sum(line.votes or 0 for _, line in labelled)
        ranked = sorted(labelled, key=lambda item: -(item[1].votes or 0))
        if not ranked or not total:
            return
        if (ranked[0][1].votes or 0) * 2 > total:
            p.add("outcome", result_set_id=result_set, option_id=options[ranked[0][0]], kind="elected")
        else:
            for label, _ in ranked[:2]:
                p.add("outcome", result_set_id=result_set, option_id=options[label], kind="advanced")
        return
    for label, line in labelled:
        if line.seats:
            p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=line.seats)


def _values(p: Package, built: Built) -> None:
    if not built.values:
        return
    sets, units, options, measures, values = zip(*built.values, strict=True)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": sets,
                "unit_id": units,
                "option_id": options,
                "channel": [0] * len(values),
                "measure": measures,
                "count_round": [0] * len(values),
                "flags": [0] * len(values),
                "value": values,
            },
            schema={
                "result_set_id": pl.String(),
                "unit_id": pl.String(),
                "option_id": pl.String(),
                "channel": pl.Int16(),
                "measure": pl.Int16(),
                "count_round": pl.Int16(),
                "flags": pl.Int16(),
                "value": pl.Int64(),
            },
        ),
    )
