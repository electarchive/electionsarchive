"""Municipal elections of the Second Republic in Álava, Gipuzkoa, and Bizkaia, from the Basque Government's files.

The Departamento de Seguridad of the Basque Government publishes, for each of the three provinces, the votes of every
candidate for municipal councillor in the elections of 12 April 1931, 31 May 1931, and 23 and 26 April 1933, by
municipality and electoral district or polling place. The files rest on the provincial bulletins, the electoral
records (actas), and the contemporary press. One package per election date holds one contest per council (or per
group of districts that elect separate councillors), with the candidates as the counted options.

The 1931 municipalities are their own areas: many were later merged, renamed, or respelled, and no official
crosswalk to today's municipality codes exists.
"""

import csv
import re
import unicodedata
from collections import defaultdict
from datetime import date
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "es.euskadi_republica"
SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
PUBLISHER = "Gobierno Vasco, Departamento de Seguridad"
TITLE = "Resultados Segunda República: elecciones municipales 1931 y 1933"
PAGE = "https://hauteskundeak.euskadi.eus/informacion/resultados-segunda-republica-elecciones-municipales-1931/webelec00-content/es/"
BASE = (
    "https://hauteskundeak.euskadi.eus/contenidos/informacion/w_em_contexto_historico_munici/es_def/"
    "result_1931-1933-municipales/csv/elecciones-municipales-1931-1933-{}_c.csv"
)
PROVINCES = {
    "alava": ("01", "06aa63b2-1ccb-5884-a0fc-4e8948ca924d", "Álava"),
    "gipuzkoa": ("20", "166f0c18-3da9-59db-8117-923393a65ef1", "Gipuzkoa"),
    "bizkaia": ("48", "c2280933-72b9-5ceb-a8e2-368651361c0e", "Bizkaia"),
}
MUNICIPIO = "es.euskadi_republica_municipio"
DISTRITO = "es.euskadi_republica_distrito"
VOTES = 1
NO_PARTY = {"", "independiente", "apolitico", "indefinido", "sin definir", "autonomo"}
PARTIES = {"pnv", "anv"}
DATE = re.compile(r"\d\d/\d\d/\d{4}")
SMALL = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "a"}
BODY = "council"
NOTES = {
    date(1931, 4, 12): "The municipal elections that brought the Second Republic, held on 12 April 1931.",
    date(1931, 5, 31): "Elections of 31 May 1931, repeated or held late in municipalities where the vote of 12 April "
    "was annulled or had not taken place.",
    date(1933, 4, 23): "The elections of 23 April 1933 in the municipalities whose councillors had been named without "
    "a vote in 1931 under article 29 of the electoral law.",
    date(1933, 4, 26): "Elections of 26 April 1933 held in Álava three days after the main date.",
}
METHOD = (
    "The Basque Government's file gives each candidate's votes by electoral district or polling place. The sources are "
    "the provincial bulletins (names and votes printed in capitals), the electoral records, and the press; the file "
    "does not mark the councillors elected, so no outcomes are stored. Each voter could mark several candidates, so "
    "candidate votes do not add up to ballots, and the file gives no totals of voters, blank ballots, or valid ballots."
)


def uid(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def tidy(text: str) -> str:
    return " ".join(text.split())


def title(text: str) -> str:
    text = tidy(text)
    if any(c.islower() for c in text):
        return text
    return " ".join(w.lower() if n and w.lower() in SMALL else w.capitalize() for n, w in enumerate(text.split()))


def url(province: str) -> str:
    return BASE.format(province)


def fetch() -> None:
    for province in PROVINCES:
        raw.fetch(SOURCE, url(province), suffix=".csv")


class Council:
    """One municipality on one date: its districts, their seats, and each candidate's votes by district."""

    def __init__(self) -> None:
        self.seats: dict[str, set[int]] = defaultdict(set)
        self.rows: dict[str, dict[str, list[tuple[str, str, int | None]]]] = defaultdict(lambda: defaultdict(list))


def read() -> tuple[dict[tuple[date, str, str], Council], dict[tuple[str, str], str], list[raw.Artifact]]:
    """The councils by (date, province, municipality key), the municipality names, and the cached files."""
    councils: dict[tuple[date, str, str], Council] = defaultdict(Council)
    names: dict[tuple[str, str], str] = {}
    artifacts = []
    for province in PROVINCES:
        artifact = raw.cached(SOURCE, url(province))
        artifacts.append(artifact)
        with artifact.path.open(encoding="latin-1", newline="") as file:
            reader = csv.reader(file, delimiter=";")
            next(reader)
            for line in reader:
                muni, district, when, what, label, votes = (tidy(value) for value in line[:6])
                if not muni:
                    continue
                key = fold(muni)
                names.setdefault((province, key), title(muni))
                district = district or "Único"
                days = [date(int(x[6:]), int(x[3:5]), int(x[:2])) for x in DATE.findall(when)]
                lowered = fold(what)
                if lowered.startswith(("numero de concejales", "numero concejales")):
                    for day in days:
                        if votes.isdigit():
                            councils[(day, province, key)].seats[district].add(int(votes))
                        else:
                            councils[(day, province, key)].seats[district]
                elif lowered in {"numero de electores", "blancos"}:
                    continue
                else:
                    council = councils[(days[0], province, key)]
                    council.seats[district]
                    council.rows[district][fold(what)].append(
                        (title(what), label, int(votes) if votes.isdigit() else None)
                    )
    return councils, names, artifacts


def groups(council: Council) -> list[list[str]]:
    """Districts that share a candidate elect one set of councillors together; the others elect their own."""
    districts = sorted(set(council.seats) | set(council.rows))
    parent = {name: name for name in districts}

    def find(name: str) -> str:
        while parent[name] != name:
            name = parent[name]
        return name

    for a in districts:
        for b in districts:
            if a < b and set(council.rows.get(a, ())) & set(council.rows.get(b, ())):
                parent[find(a)] = find(b)
    found: dict[str, list[str]] = defaultdict(list)
    for name in districts:
        found[find(name)].append(name)
    return sorted(found.values(), key=lambda members: members[0])


def party_key(label: str) -> str:
    return fold(label)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The municipalities and electoral districts of Álava, Gipuzkoa, and Bizkaia as the Basque Government's files "
        "for the 1931 and 1933 municipal elections name them, without boundaries",
    )
    p.external |= {SPAIN, *(row[1] for row in PROVINCES.values())}
    p.add("scheme", code=MUNICIPIO, polity_id=SPAIN, kind="administrative", rank=3, noun="municipality in 1931-1933")
    p.add("scheme", code=DISTRITO, polity_id=SPAIN, kind="precinct", rank=5, noun="electoral district or polling place")
    _, names, _ = read()
    for (province, key), name in sorted(names.items()):
        id = uid("area", MUNICIPIO, province, key)
        p.add("area", id=id, scheme_code=MUNICIPIO)
        p.add("area_code", area_id=id, code=f"{PROVINCES[province][0]}:{key}")
        p.add("area_parent", area_id=id, parent_id=PROVINCES[province][1])
        p.name(id, name, "es", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Basque Government's results of the 1931 and 1933 municipal elections as a source, the municipal councils "
        "of the Republic, the limited-vote system, and the affiliation labels the files print",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(SPAIN)
    p.add(
        "source",
        id=uid("source", "results"),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        attribution="Origen de los datos: Gobierno Vasco, Departamento de Seguridad.",
    )
    body = uid("body", "council")
    p.add("body", id=body, polity_id=SPAIN, kind=BODY)
    p.name(body, "Municipal councils of the Second Republic", "en", "official")
    system = uid("system", "limited")
    p.add("electoral_system", id=system, polity_id=SPAIN, rules={"formula": "limited"}, rules_version=1)
    p.name(system, "Limited vote for municipal councillors under the electoral law of 1907", "en", "official")
    p.add(
        "note",
        id=uid("note", system),
        entity_id=system,
        kind="methodology",
        body_md=(
            "Each municipality or electoral district elected its councillors by limited vote: a voter marked fewer "
            "candidates than there were seats, and the candidates with the most votes won (Ley Electoral de 8 de "
            "agosto de 1907)."
        ),
        lang="en",
    )
    labels: dict[str, str] = {}
    for council in read()[0].values():
        for rows in council.rows.values():
            for entries in rows.values():
                for _name, label, _votes in entries:
                    if party_key(label) not in NO_PARTY:
                        labels.setdefault(party_key(label), label)
    for key, label in sorted(labels.items()):
        id = uid("party", key)
        p.add("party", id=id, kind="party" if key in PARTIES else "nuance", polity_id=SPAIN)
        p.name(
            id,
            label if label.isupper() and len(label) <= 4 else label.capitalize() if label.islower() else label,
            "es",
            "official",
        )
        p.add("external_id", entity_id=id, scheme=f"{SOURCE}.label", value=key)
    return p


def election(day: date) -> Package:
    councils, names, artifacts = read()
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The municipal elections of {day} in Álava, Gipuzkoa, and Bizkaia from the Basque Government's files, by "
        "electoral district or polling place",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {SPAIN, *(row[1] for row in PROVINCES.values())}
    source = uid("source", "results")
    for artifact in artifacts:
        p.artifact(artifact, source)
    eid = uid("election", day)
    p.add("election", id=eid, polity_id=SPAIN, kind="local", date_from=day, date_to=day, status="held")
    p.name(eid, f"Municipal elections in Álava, Gipuzkoa, and Bizkaia, {day:%-d %B %Y}", "en", "official")
    p.add("slug", path=f"es/euskadi-republica/{day}-local", entity_id=eid, is_current=True)
    p.add(
        "note",
        id=uid("note", eid),
        entity_id=eid,
        kind="methodology",
        body_md=f"{NOTES.get(day, '')} {METHOD}".strip(),
        lang="en",
    )
    paths: dict[str, int] = defaultdict(int)
    people: list[dict[str, object]] = []
    for (when, province, key), council in sorted(councils.items()):
        if when != day or not council.rows:
            continue
        muni = names[(province, key)]
        area = uid("area", MUNICIPIO, province, key)
        single = len(council.seats) == 1
        for number, members in enumerate(groups(council), 1):
            with_votes = [m for m in members if council.rows.get(m)]
            if not with_votes:
                continue
            seats = {s for m in members for s in council.seats.get(m, ())}
            suffix = "" if len(groups(council)) == 1 else f"{key}:{number}"
            ckey = f"{day}:{province}:{key}{(':' + str(number)) if suffix else ''}"
            contest = uid("contest", ckey)
            label = muni if not suffix else f"{muni} ({', '.join(members)})"
            p.add(
                "contest",
                id=contest,
                election_id=eid,
                kind="body_seats",
                body_id=uid("body", "council"),
                district_area_id=area,
                magnitude=next(iter(seats)) if len(seats) == 1 else None,
                round_number=1,
                electoral_system_id=uid("system", "limited"),
                default_result_kind="counted",
            )
            p.name(contest, f"Municipal council of {label}", "en", "official")
            base = f"es/euskadi-republica/{day}-local/{changeset.slug(muni)}" + (f"-{number}" if suffix else "")
            paths[base] += 1
            p.add("slug", path=base if paths[base] == 1 else f"{base}-{province}", entity_id=contest, is_current=True)
            result_set = uid("result_set", ckey)
            p.add(
                "result_set",
                id=result_set,
                election_id=eid,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            candidates: dict[str, tuple[str, str]] = {}
            for m in members:
                for name_key, entries in council.rows.get(m, {}).items():
                    shown, party, _ = entries[0]
                    candidates.setdefault(name_key, (shown, party))
            options: dict[str, str] = {}
            for position, (name_key, (shown, party)) in enumerate(sorted(candidates.items()), 1):
                option = options[name_key] = uid("option", ckey, name_key)
                person = uid("person", ckey, name_key)
                pkey = party_key(party)
                party_id = uid("party", pkey) if pkey not in NO_PARTY else None
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_position=position,
                    ballot_label=shown,
                )
                p.add("person", id=person)
                p.name(person, shown, "es", "official")
                p.add(
                    "candidacy",
                    id=uid("candidacy", ckey, name_key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=shown,
                    member_of_party_id=party_id,
                    is_independent=pkey in {"independiente", "apolitico"},
                )
            for m in members:
                if single:
                    scheme, code, relation = MUNICIPIO, f"{day}:{PROVINCES[province][0]}:{key}", "equals"
                else:
                    scheme = DISTRITO
                    code = f"{day}:{PROVINCES[province][0]}:{key}:{fold(m)}"
                    relation = "within"
                unit = uid("unit", scheme, code)
                if not any(unit == row["id"] for row in p.rows.get("reporting_unit", [])[-40:]):
                    p.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=SPAIN,
                        scheme_code=scheme,
                        code=code,
                        area_relation=relation,
                        area_id=area,
                        kind="standard",
                    )
                for name_key, entries in council.rows.get(m, {}).items():
                    counted = [v for _, _, v in entries if v is not None]
                    if counted:
                        people.append(
                            {
                                "result_set_id": result_set,
                                "unit_id": unit,
                                "option_id": options[name_key],
                                "channel": 0,
                                "measure": VOTES,
                                "count_round": 0,
                                "flags": 0,
                                "value": sum(counted),
                            }
                        )
    p.frame("result_value", pl.DataFrame(people, schema=package.FACTS["result_value"]))
    return p


def days() -> list[date]:
    return sorted({key[0] for key, council in read()[0].items() if council.rows})


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


REASONS: dict[str, str] = {}


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        p = election(day)
        out = p.write()
        for code, count, _sample in package.check(out):
            if code not in REASONS:
                raise ValueError(f"{p.key}: {code} ({count}) is not one the source's figures can explain")
        p.write()


def emit() -> None:
    package.run("load", packages())
