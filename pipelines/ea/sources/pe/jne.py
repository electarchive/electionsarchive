"""Peru's national elections from Infogob, the Jurado Nacional de Elecciones' election archive.

Infogob serves each election's totals per constituency through JSON endpoints that its own pages call with a page
token in the body; any token the site has issued is accepted, so `TOKEN` is a fixed one and every request is a POST
cached by its body. This module covers the national processes (presidential rounds, congress, the Senate, the
Chamber of Deputies, the constituent assemblies, the Andean Parliament, and national referendums) from 1931 to 2021.
Infogob publishes them only at the constituency; finer counts are not available here.
"""

import functools
import html
import json
import re
from collections import Counter
from datetime import date
from typing import NamedTuple
from urllib.parse import quote
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "pe.jne"
BASE = "https://infogob.jne.gob.pe"
TOKEN = "GAATxwlNrPU=Aw"
PERU = "sPpbSaCqXnM=pa"
NONE = "7x8igXWeaZ8=8X"
EMPTY = "HOvv9BJTsTA=vB"
TYPES = {
    "PrO34XpiKLg=OX": "presidential",
    "dHU0mWeyL0k=UW": "congressional",
    "5srS8PzIpTg=rP": "andean",
    "ozbDLopjV7s=bo": "regional",
    "sY5dLXlhrMU=5X": "provincial",
    "L5MJsms6q5Y=Mm": "district",
    "WIHkyMA8HI8=HM": "complementary",
    "rWwQzpPNurI=wp": "recall",
    "o4Gm8EQvvw0=GE": "referendum",
    "rqThaZb/DOg=TZ": "new_municipal",
}
NATIONAL = ("presidential", "congressional", "andean", "referendum")
PROCESSES = f"{BASE}/Localidad/_ProcesosElectoralesLocalidad?istrParameters={quote(PERU, safe='')}"
ELECTIONS = f"{BASE}/Eleccion/ListarElecciones"
PROVINCES = f"{BASE}/Eleccion/ListarProvincias"
RESULTS = f"{BASE}/Eleccion/RecuperarDatosCandidatosResultados"


def _post(url: str, params: str | None = None, *, suffix: str = ".json") -> raw.Artifact:
    body: dict[str, object] = {"token": TOKEN} if params is None else {"istrParameters": params, "token": TOKEN}
    return raw.fetch(SOURCE, url, suffix=suffix, body=body)


def _cached(url: str, params: str | None = None) -> raw.Artifact:
    body: dict[str, object] = {"token": TOKEN} if params is None else {"istrParameters": params, "token": TOKEN}
    return raw.cached(SOURCE, url, body)


def _data(artifact: raw.Artifact) -> object:
    payload = json.loads(artifact.path.read_bytes())
    if payload.get("Estado") != "success":
        raise ValueError(f"{artifact.url}: {payload.get('Mensaje')}")
    return payload["Data"]


def ficha_url(election: str) -> str:
    return f"{BASE}/Eleccion/FichaEleccion/x_candidatos-y-resultados_{election.replace('/', '@')}"


def partial_url(election: str, flags: dict[str, str]) -> str:
    params = (
        flags["IdAmbito"]
        + flags["FgDistMultiple"]
        + flags["FgVotoPreferencial"]
        + flags["FgEleccionLista"]
        + election
        + "@"
        + flags["IdGrupoEleccion"]
    )
    return f"{BASE}/Eleccion/_CandidatosResultadosEleccion?istrParameters={quote(params, safe='')}"


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def processes(fetch: bool = False) -> list[dict[str, str]]:
    artifact = _post(PROCESSES, suffix=".html") if fetch else _cached(PROCESSES)
    page = artifact.path.read_text(encoding="utf-8")
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.DOTALL):
        cells = [_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)]
        if len(cells) >= 5:
            rows.append({"id": cells[0], "group": cells[1], "date": cells[2], "name": cells[3], "comment": cells[4]})
    return rows


def catalog(fetch: bool = False) -> dict[str, list[tuple[str, str]]]:
    found: dict[str, list[tuple[str, str]]] = {}
    for code, kind in TYPES.items():
        artifact = _post(ELECTIONS, code) if fetch else _cached(ELECTIONS, code)
        data = _data(artifact)
        assert isinstance(data, list)
        found[kind] = [(str(row["Value"]), str(row["Text"])) for row in data if row["Value"] != NONE]
    return found


def flags(election: str, fetch: bool = False) -> dict[str, str]:
    url = ficha_url(election)
    artifact = raw.fetch(SOURCE, url, suffix=".html") if fetch else raw.cached(SOURCE, url)
    page = artifact.path.read_text(encoding="utf-8")
    found = {
        key: html.unescape(value)
        for key, value in re.findall(r'<input type="hidden" id="(\w+)"(?: name="\w+")? value="([^"]*)"', page)
    }
    if found.get("IdEleccion") != election:
        raise ValueError(f"{url}: the page names election {found.get('IdEleccion')}")
    return found


def options(page: str, select: str) -> list[tuple[str, str]]:
    match = re.search(rf'<select[^>]*id="{select}"[^>]*>(.*?)</select>', page, flags=re.DOTALL)
    if match is None:
        return []
    return [
        (html.unescape(value), _text(text))
        for value, text in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)', match.group(1))
        if html.unescape(value) != NONE
    ]


def districts(election: str, info: dict[str, str], fetch: bool = False) -> list[tuple[str, str, str | None]]:
    """The localities whose results make up one election: (locality, name, region name)."""
    scope = info["IdAmbito"]
    if scope == "1":
        return [(PERU, "PERU", None)]
    url = partial_url(election, info)
    artifact = _post(url, suffix=".html") if fetch else _cached(url)
    page = artifact.path.read_text(encoding="utf-8")
    if scope in ("2", "5"):
        return [(value, text, None) for value, text in options(page, "IdDistritoElectoral")]
    if scope == "3":
        found = []
        for region, name in options(page, "IdRegion"):
            params = f"{election}@{region}"
            data = _data(_post(PROVINCES, params) if fetch else _cached(PROVINCES, params))
            assert isinstance(data, list)
            found += [
                (str(row["Value"]), _text(str(row["Text"])), name) for row in data if row["Value"] not in (NONE, EMPTY)
            ]
        return found
    raise ValueError(f"{election}: scope {scope} is not handled")


def results(election: str, locality: str, group: str, fetch: bool = False) -> dict[str, object]:
    params = f"{election}@{locality}@{group}"
    data = _data(_post(RESULTS, params) if fetch else _cached(RESULTS, params))
    assert isinstance(data, dict)
    return data


def national() -> list[dict[str, str]]:
    listed = catalog()
    ids = {value for kind in NATIONAL for value, _ in listed[kind]}
    return [row for row in processes() if row["id"] in ids]


def fetch() -> None:
    listed = catalog(fetch=True)
    rows = processes(fetch=True)
    ids = {value for kind in NATIONAL for value, _ in listed[kind]}
    missing = ids - {row["id"] for row in rows}
    if missing:
        raise ValueError(f"national elections missing from the process list: {sorted(missing)}")
    for n, row in enumerate(row for row in rows if row["id"] in ids):
        info = flags(row["id"], fetch=True)
        places = districts(row["id"], info, fetch=True)
        for locality, _, _ in places:
            results(row["id"], locality, info["IdGrupoEleccion"], fetch=True)
        print(f"{n + 1}: {row['date']} {row['name']}: {len(places)} localities", flush=True)


# Package building

PERU_POLITY = str(uuid5(package.NAMESPACE, "polity\x1fPE"))
BLANK = "sXGfB2BCKak=G2"
NULL = "HZvD6XyHdHA=vX"
SPECIAL = {BLANK: 6, NULL: 5}
LIMA_PROVINCE = "tHBk9w8XY0o=Bw"
ABROAD = "RESIDENTES EN EL EXTRANJERO"
MINOR = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "para", "por", "a", "al"}
METHOD = (
    "Totals as published by the Jurado Nacional de Elecciones in Infogob, its election archive, for each contest's "
    "constituency. Registered voters are the electoral roll, ballots cast are the votes cast (votos emitidos), and "
    "blank and null votes are stored as blank and invalid ballots. Infogob does not publish these elections below "
    "the constituency; the polling-station counts of the Oficina Nacional de Procesos Electorales are not stored here."
)
ELECTED_ONLY = (
    "Infogob publishes only the members elected in this constituency, with the votes each received, and not the "
    "votes of every list; no vote totals are stored for this contest."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
PRESIDENCY = key("office", "president")
BODIES = {
    "congress": ("unicameral", "Congress of the Republic", "Congreso de la República"),
    "senate": ("upper_house", "Senate", "Senado"),
    "deputies": ("lower_house", "Chamber of Deputies", "Cámara de Diputados"),
    "constituent-1931": ("constituent_assembly", "Constituent Congress", "Congreso Constituyente"),
    "constituent-1978": ("constituent_assembly", "Constituent Assembly", "Asamblea Constituyente"),
    "constituent-1992": (
        "constituent_assembly",
        "Democratic Constituent Congress",
        "Congreso Constituyente Democrático",
    ),
    "andean": ("unicameral", "Andean Parliament (Peruvian members)", "Parlamento Andino"),
}
SYSTEMS: list[tuple[str, str, date, date | None, dict[str, object], str]] = [
    (
        "president-1931",
        "president",
        date(1931, 1, 1),
        date(1980, 1, 1),
        {"formula": "contingent", "majority": 1 / 3, "chooser": "legislature", "among": 3},
        (
            "A candidate who won a third of the valid votes was elected; otherwise Congress chose among the three "
            "leading candidates."
        ),
    ),
    (
        "president-1980",
        "president",
        date(1980, 1, 1),
        date(1985, 1, 1),
        {"formula": "two_round", "majority": 0.36, "advance": 2},
        (
            "Under the transitory rule of the 1979 Constitution, a candidate who won 36% of the valid votes in 1980 was "
            "elected; otherwise the two leading candidates went to a second round."
        ),
    ),
    (
        "president-1985",
        "president",
        date(1985, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate who wins more than half of the valid votes is elected; otherwise the two leading candidates go "
            "to a second round. In 1985 the share was computed over all votes cast, blank and null votes included."
        ),
    ),
    (
        "list-1963",
        "list",
        date(1963, 1, 1),
        date(2006, 1, 1),
        {"formula": "dhondt"},
        (
            "Seats are allocated by D'Hondt among the lists in each constituency, with preferential votes ordering the "
            "candidates within a list from 1985 on."
        ),
    ),
    (
        "list-2006",
        "list",
        date(2006, 1, 1),
        date(2011, 1, 1),
        {"formula": "dhondt", "threshold": {"percent": 4, "level": "party", "scope": "national"}},
        (
            "Seats are allocated by D'Hondt in each constituency among the parties that won 4% of the valid votes "
            "nationally or seats in more than one constituency, with preferential votes ordering candidates."
        ),
    ),
    (
        "list-2011",
        "list",
        date(2011, 1, 1),
        None,
        {"formula": "dhondt", "threshold": {"percent": 5, "level": "party", "scope": "national"}},
        (
            "Seats are allocated by D'Hondt in each constituency among the parties that won 5% of the valid votes "
            "nationally and at least five seats in more than one constituency, with preferential votes ordering "
            "candidates."
        ),
    ),
    (
        "referendum",
        "referendum",
        date(1993, 1, 1),
        None,
        {"formula": "referendum", "majority": {"share": 0.5}},
        "A question is approved when more than half of the valid votes are yes.",
    ),
]


def system(kind: str, day: date) -> str | None:
    for name, family, since, until, _, _ in SYSTEMS:
        if family == kind and since <= day and (until is None or day < until):
            return key("electoral_system", name)
    return None


def title(text: str) -> str:
    words = " ".join(text.split()).lower().split(" ")
    return " ".join(
        word if index and word in MINOR else word[:1].upper() + word[1:] for index, word in enumerate(words)
    )


def number(text: object) -> int | None:
    if text is None:
        return None
    digits = str(text).replace(",", "").strip()
    return int(digits) if digits not in ("", "-") else None


def day(text: str) -> date:
    d, m, y = text.split("/")
    return date(int(y), int(m), int(d))


def tail(path: str, marker: str) -> str:
    return path.rsplit(marker, 1)[1]


class Contest(NamedTuple):
    process: dict[str, str]
    kind: str
    body: str | None
    locality: str
    label: str
    region: tuple[str, str] | None
    data: dict[str, object]


def body_of(name: str) -> tuple[str, str | None]:
    if "PRESIDENCIAL" in name:
        return "president", None
    if "PARLAMENTO ANDINO" in name:
        return "andean", "andean"
    if "REFERENDUM" in name:
        return "referendum", None
    if "SENADORES" in name:
        return "list", "senate"
    if "DIPUTADOS" in name:
        return "list", "deputies"
    if "CONSTITUYENTE" in name:
        year = int(re.findall(r"\b(19\d\d)\b", name)[0])
        return "list", "constituent-1931" if year < 1950 else "constituent-1978" if year < 1990 else "constituent-1992"
    if "CONGRESAL" in name:
        return "list", "congress"
    raise ValueError(f"{name}: unknown kind of election")


@functools.cache
def contests() -> list[Contest]:
    found = []
    for row in national():
        info = flags(row["id"])
        kind, body = body_of(row["name"])
        for locality, label, region in districts(row["id"], info):
            data = results(row["id"], locality, info["IdGrupoEleccion"])
            general = data.get("DatosGenerales") or {}
            assert isinstance(general, dict)
            if not data.get("Resultados") and not data.get("Electos") and not number(general.get("NuElectores")):
                continue
            parent = None
            if region is not None:
                parent = next(
                    (value, name)
                    for value, name in options(
                        _cached(partial_url(row["id"], info)).path.read_text(encoding="utf-8"), "IdRegion"
                    )
                    if name == region
                )
            found.append(Contest(row, kind, body, locality, label, parent, data))
    return found


def scheme_of(contest: Contest) -> str:
    return "pe.provincia" if contest.region is not None else "pe.circunscripcion"


def area_code(locality: str, label: str) -> str:
    if label == "PERU":
        return "nacional"
    if locality == LIMA_PROVINCE and label == "LIMA":
        return "lima-metropolitana"
    return changeset.slug(label)


def area_of(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def packages() -> list[str]:
    return [
        "pe.jne/geo",
        "pe.jne/registry",
        *(f"pe.jne/{d}" for d in sorted({day(c.process["date"]) for c in contests()})),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for d in sorted({day(c.process["date"]) for c in contests()}):
        election_day(d).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        "pe.jne/geo",
        SOURCE,
        "Peru's electoral constituencies from 1931 to 2021 as the Jurado Nacional de Elecciones names them in Infogob",
    )
    p.external.add(PERU_POLITY)
    p.add(
        "scheme",
        code="pe.circunscripcion",
        polity_id=PERU_POLITY,
        kind="electoral_district",
        rank=1,
        noun="constituency",
    )
    p.add(
        "scheme",
        code="pe.provincia",
        polity_id=PERU_POLITY,
        kind="electoral_district",
        rank=2,
        noun="provincial constituency",
    )
    seen: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    codes: dict[tuple[str, str], str] = {}

    def place(scheme: str, locality: str | None, label: str, parent: str | None) -> None:
        code = area_code(locality or "", label)
        if locality is not None:
            known = codes.setdefault((scheme, code), locality)
            if known != locality and label != "PERU":
                raise ValueError(f"{scheme} {code} names two localities: {known} and {locality}")
        seen.setdefault((scheme, code), (label, locality or "", parent))

    for c in contests():
        if c.region is not None:
            place("pe.circunscripcion", c.region[0], c.region[1], None)
            place("pe.provincia", c.locality, c.label, area_of("pe.circunscripcion", area_code(*c.region)))
        else:
            place("pe.circunscripcion", c.locality, c.label, None)
        if c.kind == "referendum" and c.locality != PERU:
            for row in _rows(c):
                place("pe.circunscripcion", None, str(row["TxRegionRef"]), None)
    for (scheme, code), (label, locality, parent) in sorted(seen.items()):
        id = area_of(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        if parent is not None:
            p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, "Perú" if label == "PERU" else title(label), "es", "official")
        if label == "PERU":
            p.name(id, "Peru (national constituency)", "en", "english", False)
    return p


def _rows(c: Contest) -> list[dict[str, object]]:
    rows = c.data.get("Resultados") or []
    assert isinstance(rows, list)
    return rows


def _electos(c: Contest) -> list[dict[str, object]]:
    rows = c.data.get("Electos") or []
    assert isinstance(rows, list)
    return rows


def _general(c: Contest) -> dict[str, object]:
    general = c.data.get("DatosGenerales") or {}
    assert isinstance(general, dict)
    return general


def party(org: str) -> str:
    return key("party", org)


def org_of(row: dict[str, object]) -> str:
    return tail(str(row["TxRutaOrgPol"]), "_acerca-de_").replace("@", "/")


def person(path: str) -> str:
    return key("person", tail(path, "_historial-partidario_"))


def registry() -> Package:
    p = Package(
        "pe.jne/registry",
        SOURCE,
        "Peru's presidency, legislatures, electoral systems, parties, and elected members from the Jurado Nacional "
        "de Elecciones' Infogob archive",
        depends=["pe.jne/geo"],
    )
    p.external.add(PERU_POLITY)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Jurado Nacional de Elecciones",
        title="Infogob – Observatorio para la Gobernabilidad",
        url=BASE,
        license="Official public record, no license stated",
        license_url=BASE,
        attribution="Jurado Nacional de Elecciones, Infogob",
    )
    index = json.loads((raw.DATA / "raw" / SOURCE / "index.json").read_text())
    cited: set[str] = set()
    for url_key, entry in sorted(index.items()):
        body = entry.get("body")
        url = url_key.split("#", 1)[0] if body else url_key
        artifact = raw.cached(SOURCE, url, json.loads(body) if body else None)
        if artifact.storage_key not in cited:
            cited.add(artifact.storage_key)
            p.artifact(artifact, SOURCE_ID)
    p.add("office", id=PRESIDENCY, polity_id=PERU_POLITY, kind="head_of_state")
    p.name(PRESIDENCY, "President of Peru", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República", "es", "native", False)
    for code, (kind, english, spanish) in BODIES.items():
        id = key("body", code)
        p.add("body", id=id, polity_id=PERU_POLITY, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    for name, _, since, until, rules, method in SYSTEMS:
        id = key("electoral_system", name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=PERU_POLITY,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    names: dict[str, list[tuple[date, str]]] = {}
    people: dict[str, list[tuple[date, str]]] = {}
    for c in contests():
        when = day(c.process["date"])
        for row in _rows(c):
            org = row.get("IdOrgPol")
            if org and org not in SPECIAL:
                names.setdefault(str(org), []).append((when, " ".join(str(row["TxOrgPol"]).split())))
        for row in _electos(c):
            org = org_of(row)
            names.setdefault(org, []).append((when, " ".join(str(row["TxOrgPol"]).split())))
            people.setdefault(person(str(row["TxRutaPolitico"])), []).append(
                (when, " ".join(str(row["TxCandidato"]).split()))
            )
    for org, seen in sorted(names.items()):
        id = party(org)
        p.add("party", id=id, kind="party", polity_id=PERU_POLITY)
        p.add("external_id", entity_id=id, scheme="pe.jne.orgpol", value=org)
        latest = max(seen)[1]
        p.name(id, latest, "es", "official")
        for text in sorted({text for _, text in seen} - {latest}):
            p.name(id, text, "es", "official", False)
    for id, seen in sorted(people.items()):
        p.add("person", id=id)
        latest = max(seen)[1]
        p.name(id, title(latest), "es", "official")
    return p


def _fold(text: str) -> str:
    return changeset.slug(text).replace("-", " ")


def election_kind(c: Contest) -> str:
    name = c.process["name"]
    if "SEGUNDA VUELTA" in name:
        return "runoff"
    if c.kind == "referendum":
        return "referendum"
    if "COMPLEMENTARIAS" in name or "PARCIALES" in name:
        return "by_election"
    return "general"


ANNULLED = re.compile(r"^(LAS ELECCIONES FUERON ANULADAS|ELECCI[OÓ]N ANULADA|SE ANUL[OÓ] LA ELECCI[OÓ]N SIN)")
SLUGS = {
    "congress": "congress",
    "senate": "senate",
    "deputies": "chamber-of-deputies",
    "constituent-1931": "constituent-congress",
    "constituent-1978": "constituent-assembly",
    "constituent-1992": "democratic-constituent-congress",
    "andean": "andean-parliament",
}


def election_day(when: date) -> Package:
    here = [c for c in contests() if day(c.process["date"]) == when]
    p = Package(
        f"pe.jne/{when}",
        SOURCE,
        f"Peru's national elections of {when} by constituency, from the Jurado Nacional de Elecciones' Infogob archive",
        depends=["pe.jne/geo", "pe.jne/registry"],
    )
    p.external.add(PERU_POLITY)
    used = {(c.process["id"], c.locality) for c in here}
    for process_id, locality in sorted(used):
        c = next(c for c in here if c.process["id"] == process_id and c.locality == locality)
        info = flags(process_id)
        p.uses(raw.cached(SOURCE, ficha_url(process_id)))
        p.uses(_cached(RESULTS, f"{process_id}@{locality}@{info['IdGrupoEleccion']}"))
        if info["IdAmbito"] != "1":
            p.uses(_cached(partial_url(process_id, info)))
    p.uses(_cached(PROCESSES))
    elections: dict[str, str] = {}
    paths: dict[str, str] = {}
    by_kind: dict[str, list[Contest]] = {}
    for c in here:
        by_kind.setdefault(election_kind(c), []).append(c)
    word = {"general": "general", "runoff": "runoff", "referendum": "referendum", "by_election": "by-election"}
    for kind, members in sorted(by_kind.items()):
        id = elections[kind] = key("election", when, kind)
        processes_here = {c.process["id"]: c.process for c in members}
        annulled = all(ANNULLED.match(row["comment"]) for row in processes_here.values())
        p.add(
            "election",
            id=id,
            polity_id=PERU_POLITY,
            kind=kind,
            date_from=when,
            date_to=when,
            status="annulled" if annulled else "held",
        )
        bodies = {c.body for c in members}
        english = {
            "runoff": f"{when.year} Peruvian presidential election, second round",
            "referendum": f"{when.year} Peruvian referendum",
            "by_election": f"{when.year} Peruvian supplementary parliamentary elections",
        }.get(kind)
        if english is None:
            english = (
                f"{when.year} Peruvian Constituent Assembly election"
                if bodies == {"constituent-1978"}
                else f"{when.year} Peruvian Democratic Constituent Congress election"
                if bodies == {"constituent-1992"}
                else f"{when.year} Peruvian general election"
            )
        p.name(id, english, "en", "official")
        first = min(processes_here.values(), key=lambda row: row["name"])
        p.name(id, title(first["name"].split(" - ")[0]), "es", "native", False)
        path = paths[kind] = f"pe/{when}-{word[kind]}"
        p.add("slug", path=path, entity_id=id, is_current=True)
        for process_id, row in sorted(processes_here.items()):
            p.add(
                "note",
                id=key("note", id, process_id),
                entity_id=id,
                kind="context",
                body_md=f"{title(row['name'])}: {row['comment'][:1].upper()}{row['comment'][1:].lower()}",
                lang="es",
            )
    units: set[str] = set()
    mismatched: Counter[str] = Counter()
    over: Counter[str] = Counter()
    for c in here:
        _contest(p, c, when, elections[election_kind(c)], paths[election_kind(c)], units, mismatched, over)
    for result_set in mismatched:
        p.add(
            "note",
            id=key("note", result_set, "sum"),
            entity_id=result_set,
            kind="caveat",
            body_md="Infogob's votes for the options plus blank and null votes do not add up to the votes cast it "
            "prints for this contest. The figures are stored as printed.",
            lang="en",
        )
    if over:
        p.accept(
            "accounting.turnout",
            f"Infogob prints more votes cast than registered voters in {over.total()} constituencies; "
            "stored as printed",
        )
    return p


def _unit(p: Package, c: Contest, when: date, units: set[str]) -> str:
    scheme = scheme_of(c)
    code = area_code(c.locality, c.label)
    id = key("unit", when, scheme, code)
    if id not in units:
        units.add(id)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=PERU_POLITY,
            scheme_code=scheme,
            code=f"{when}:{code}",
            area_relation="equals",
            area_id=area_of(scheme, code),
            kind="abroad_mission" if c.label == ABROAD else "standard",
        )
    return id


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
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


def _set(p: Package, election: str, contest: str, complete: bool) -> str:
    result_set = key("result_set", contest)
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
        completeness=1.0 if complete else None,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    if not complete:
        p.add(
            "note",
            id=key("note", result_set, "elected"),
            entity_id=result_set,
            kind="caveat",
            body_md=ELECTED_ONLY,
            lang="en",
        )
    return result_set


def _accounting(
    p: Package, c: Contest, result_set: str, unit: str, options: int, mismatched: Counter[str], over: Counter[str]
) -> None:
    general = _general(c)
    registered = number(general.get("NuElectores")) or None
    cast = number(general.get("NuVotosEmi")) or None
    _value(p, result_set, unit, None, 2, registered)
    _value(p, result_set, unit, None, 3, cast)
    special = {
        SPECIAL[str(row["IdOrgPol"])]: number(row["NuOrgVotos"]) for row in _rows(c) if row["IdOrgPol"] in SPECIAL
    }
    for measure, value in special.items():
        _value(p, result_set, unit, None, measure, value)
    if cast is not None and options + sum(v or 0 for v in special.values()) != cast:
        mismatched[result_set] += 1
    if cast is not None and registered is not None and cast > registered:
        over[result_set] += 1


def _contest(
    p: Package,
    c: Contest,
    when: date,
    election: str,
    path: str,
    units: set[str],
    mismatched: Counter[str],
    over: Counter[str],
) -> None:
    if c.kind == "referendum":
        _referendum(p, c, when, election, path, units, mismatched)
        return
    scheme = scheme_of(c)
    district = area_of(scheme, area_code(c.locality, c.label))
    national = c.locality == PERU
    rows = [row for row in _rows(c) if row["IdOrgPol"] not in SPECIAL]
    electos = _electos(c)
    contest = key("contest", c.process["id"], c.locality)
    if c.kind == "president":
        runoff = election_kind(c) == "runoff"
        previous = None
        if runoff:
            first = max(
                (
                    other
                    for other in contests()
                    if other.kind == "president"
                    and election_kind(other) != "runoff"
                    and day(other.process["date"]) < when
                ),
                key=lambda other: day(other.process["date"]),
            )
            previous = key("contest", first.process["id"], first.locality)
            p.external.add(previous)
            p.depends.append(f"pe.jne/{day(first.process['date'])}")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=PRESIDENCY,
            district_area_id=district,
            magnitude=1,
            round_number=2 if runoff else 1,
            previous_round_id=previous,
            electoral_system_id=system("president", when),
            default_result_kind="counted",
        )
        p.name(
            contest,
            f"{when.year} Peruvian presidential election" + (", second round" if runoff else ""),
            "en",
            "official",
        )
        p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    else:
        assert c.body is not None
        listed = bool(rows)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation" if listed else "body_seats",
            body_id=key("body", c.body),
            district_area_id=district,
            magnitude=sum("SUPLENTE" not in str(row["TxCargoElecto"]) for row in electos) or None,
            round_number=1,
            electoral_system_id=system("list", when) if listed else None,
            default_result_kind="counted",
        )
        english = BODIES[c.body][1]
        place = title(c.label) if c.region is None else f"{title(c.label)}, {title(c.region[1])}"
        p.name(contest, english if national else place, "en" if national else "es", "official")
        tail_slug = SLUGS[c.body] + (
            ""
            if national
            else f"-{changeset.slug(c.label)}"
            if c.region is None
            else f"-{changeset.slug(c.region[1])}-{changeset.slug(c.label)}"
        )
        if c.locality == LIMA_PROVINCE and c.region is None:
            tail_slug = f"{SLUGS[c.body]}-lima-metropolitana"
        p.add("slug", path=f"{path}/{tail_slug}", entity_id=contest, is_current=True)
    result_set = _set(p, election, contest, bool(rows))
    unit = _unit(p, c, when, units)
    options: dict[str, str] = {}

    def option(org: str, label: str) -> str:
        if org not in options:
            id = options[org] = key("option", contest, org)
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="ticket" if c.kind == "president" else "list",
                party_id=party(org),
                ballot_label=" ".join(label.split()),
            )
        return options[org]

    total = 0
    for row in rows:
        votes = number(row["NuOrgVotos"])
        _value(p, result_set, unit, option(str(row["IdOrgPol"]), str(row["TxOrgPol"])), 1, votes)
        total += votes or 0
    _shares(p, result_set, rows, total, when)
    _accounting(p, c, result_set, unit, total, mismatched, over)
    seats: Counter[str] = Counter()
    for row in electos:
        org = org_of(row)
        chosen = option(org, str(row["TxOrgPol"]))
        office = str(row["TxCargoElecto"])
        role = (
            "substitute"
            if "SUPLENTE" in office
            else ("running_mate" if "VICEPRESIDENTE" in office else "head")
            if c.kind == "president"
            else "list_member"
        )
        candidate = person(str(row["TxRutaPolitico"]))
        candidacy = key("candidacy", contest, candidate)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=candidate,
            option_id=chosen,
            role=role,
            ballot_name=" ".join(str(row["TxCandidato"]).split()),
            member_of_party_id=party(org),
        )
        if role == "list_member" or (role == "head" and c.kind != "president"):
            seats[chosen] += 1
            p.add("outcome", result_set_id=result_set, option_id=chosen, candidacy_id=candidacy, kind="elected")
    if c.kind == "president":
        winner = _president(p, c, when, result_set, options, rows)
        heads = {org_of(row) for row in electos if "VICE" not in str(row["TxCargoElecto"])}
        if heads and heads != {winner}:
            raise ValueError(f"{c.process['name']}: Infogob lists {heads} as elected, its note names {winner}")
        return
    for chosen, n in seats.items():
        p.add("outcome", result_set_id=result_set, option_id=chosen, kind="won", seats=n)


def _shares(p: Package, result_set: str, rows: list[dict[str, object]], total: int, when: date) -> None:
    if not rows or not total or when.year == 1985:
        return
    top = max(rows, key=lambda row: number(row["NuOrgVotos"]) or 0)
    share = float(str(top["NuOrgPorc"]).rstrip("%"))
    if not share:
        return
    implied = round((number(top["NuOrgVotos"]) or 0) * 100 / share)
    if abs(implied - total) > max(50, total // 1000):
        p.add(
            "note",
            id=key("note", result_set, "shares"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"Infogob's printed percentages imply about {implied:,} valid votes, while the lists it prints "
            f"add up to {total:,}. The vote counts are stored as printed.",
            lang="en",
        )


WINNER = re.compile(r"(?:GANADORA FUE|RESULTANDO GANADOR) (.+?) CON EL")


def _president(
    p: Package, c: Contest, when: date, result_set: str, options: dict[str, str], rows: list[dict[str, object]]
) -> str | None:
    labels = {str(row["IdOrgPol"]): _fold(str(row["TxOrgPol"])) for row in rows}
    found = WINNER.search(c.process["comment"])
    if found is not None:
        text = _fold(found.group(1))
        matches = [org for org, label in labels.items() if label and label in text]
        if len(matches) != 1:
            matches = sorted(matches, key=lambda org: -len(labels[org]))[:1]
        if not matches:
            raise ValueError(f"{c.process['name']}: no option matches the declared winner {found.group(1)!r}")
        for org, id in options.items():
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=id,
                kind="elected" if org == matches[0] else "lost",
            )
        return matches[0]
    later = [
        other
        for other in contests()
        if other.kind == "president"
        and election_kind(other) == "runoff"
        and day(other.process["date"]) > when
        and day(other.process["date"]).year == when.year
    ]
    if later:
        advanced = {str(row["IdOrgPol"]) for row in _rows(later[0]) if row["IdOrgPol"] not in SPECIAL}
        if not advanced <= set(options):
            raise ValueError(f"{c.process['name']}: second-round options {advanced - set(options)} did not run")
        for org, id in options.items():
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=id,
                kind="advanced" if org in advanced else "eliminated",
            )
    return None


def _referendum(
    p: Package,
    c: Contest,
    when: date,
    election: str,
    path: str,
    units: set[str],
    mismatched: Counter[str],
) -> None:
    general = _general(c)
    rows = _rows(c)
    regional = c.locality != PERU
    questions = [rows] if regional else [[row] for row in rows]
    for index, members in enumerate(questions, start=1):
        contest = key("contest", c.process["id"], c.locality, index)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=area_of("pe.circunscripcion", area_code(c.locality, c.label)),
            round_number=1,
            electoral_system_id=system("referendum", when),
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        if regional:
            name = title(c.label.removeprefix("REGIÓN ").removeprefix("REGION "))
            slug = f"integration-{changeset.slug(name)}"
        elif len(questions) > 1:
            name, slug = f"Pregunta {index}", f"question-{index}"
        else:
            name, slug = title(c.process["name"].split(" - ")[0]), "referendum"
        p.name(contest, name, "es", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        question = str(general.get("TxPregunta") or "")
        if question and (regional or len(questions) == 1):
            p.add(
                "note",
                id=key("note", contest, "question"),
                entity_id=contest,
                kind="context",
                body_md=f"Question on the ballot: {question}",
                lang="en",
            )
        result_set = _set(p, election, contest, True)
        yes, no = key("option", contest, "yes"), key("option", contest, "no")
        p.add("contest_option", id=yes, contest_id=contest, kind="answer", ballot_position=1, ballot_label="Sí")
        p.add("contest_option", id=no, contest_id=contest, kind="answer", ballot_position=2, ballot_label="No")
        for row in members:
            if regional:
                region = str(row["TxRegionRef"])
                unit = key("unit", when, "pe.circunscripcion", area_code("", region))
                if unit not in units:
                    units.add(unit)
                    p.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=PERU_POLITY,
                        scheme_code="pe.circunscripcion",
                        code=f"{when}:{area_code('', region)}",
                        area_relation="equals",
                        area_id=area_of("pe.circunscripcion", area_code("", region)),
                        kind="standard",
                    )
            else:
                unit = _unit(p, c, when, units)
                _value(p, result_set, unit, None, 2, number(general.get("NuElectores")) or None)
            figures = {k: number(row[k]) for k in ("NuVotosSi", "NuVotosNo", "NuVotosBl", "NuVotosNl", "NuVotosEmi")}
            _value(p, result_set, unit, yes, 1, figures["NuVotosSi"])
            _value(p, result_set, unit, no, 1, figures["NuVotosNo"])
            _value(p, result_set, unit, None, 6, figures["NuVotosBl"])
            _value(p, result_set, unit, None, 5, figures["NuVotosNl"])
            _value(p, result_set, unit, None, 3, figures["NuVotosEmi"])
            parts = sum(figures[k] or 0 for k in ("NuVotosSi", "NuVotosNo", "NuVotosBl", "NuVotosNl"))
            if parts != figures["NuVotosEmi"]:
                mismatched[result_set] += 1
        if regional:
            p.add("declared", result_set_id=result_set, option_id=None, measure=3, value=number(general["NuVotosEmi"]))
        else:
            approved = str(members[0]["TxResultado"]) == "APROBADO"
            p.add("outcome", result_set_id=result_set, option_id=yes, kind="won" if approved else "lost")
            p.add("outcome", result_set_id=result_set, option_id=no, kind="lost" if approved else "won")
