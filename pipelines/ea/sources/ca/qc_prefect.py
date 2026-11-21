"""Quebec MRC prefects elected by universal suffrage, 2005 to 2025.

Most regional county municipalities (MRCs) choose their prefect from among the mayors on the council, but some hold a
vote of all electors on the day of the municipal general election. The Ministère des Affaires municipales et de
l'Habitation publishes one results page per general election from 2005 to 2017, with each MRC's electors, valid and
rejected votes, and candidates. The 2005 page lists the candidates without their votes. From 2021, Élections Québec
publishes a JSON file per MRC and election with every candidate's votes and status. Each prefect post is a contest
for one seat decided by plurality, so each contest has one result set. A post whose only candidate was acclaimed
carries no votes and a `tacit` result set.

Boundaries of the MRCs come from the Ministère des Ressources naturelles et des Forêts, in its administrative
divisions at 1:100,000 (CC BY 4.0), matched by the MRC's geographic code.
"""

import functools
import io
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import uuid5

import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ca.qc_prefect"
SHAPES = "ca.qc_municipal.sda"
SDA = "https://diffusion.mern.gouv.qc.ca/diffusion/RGQ/Vectoriel/Theme/Regional/SDA_100k/SHP/BDAT(adm)_SHP.zip"
SDA_DATASET = "https://www.donneesquebec.ca/recherche/dataset/decoupages-administratifs"
OPEN = "https://donnees.electionsquebec.qc.ca/production/municipal/scrutin/"
OPEN_PAGE = "https://www.electionsquebec.qc.ca/donnees-ouvertes/"
PAGES = (
    "https://www.quebec.ca/gouvernement/gestion-municipale/organisation-municipale/democratie-municipale/"
    "elections-municipales/resultats/"
)
PAGE = {
    2005: "mrc-prefet-2005",
    2009: "prefet-mrc-2009",
    2013: "prefet-mrc-2013",
    2017: "prefet-mrc-2017",
}
DAYS = {
    2005: date(2005, 11, 6),
    2009: date(2009, 11, 1),
    2013: date(2013, 11, 3),
    2017: date(2017, 11, 5),
}
# Production holds Canada, Quebec, and its province area under the ids their first pipeline gave them.
CANADA = "01a13fb6-ea9b-72f8-8170-25b12e45cb98"
QUEBEC = "01a13fb6-eab7-7501-a0f9-4903a58f045b"
QUEBEC_AREA = "01a13fb6-eab7-7501-a0f9-48f63db097d3"
STATUS = {"ESC": "elected", "ESO": "elected", "CAN": "lost", "DES": "withdrew"}
SYSTEM_NOTE = (
    "The prefect of a regional county municipality that elects one by universal suffrage is chosen by a vote of all "
    "the electors of the MRC: every elector marks one candidate, and the candidate with the most votes wins. A post "
    "with a single candidate is filled without a vote."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


MAMH = key("source", "mamh")
ELECTIONS_QUEBEC = key("source", "elections-quebec")
MRNF = key("source", "mrnf")
SYSTEM = key("electoral_system", "plurality")


def fold(text: str) -> str:
    text = text.replace("’", "'")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def fetch() -> None:
    for page in PAGE.values():
        raw.fetch(SOURCE, PAGES + page, suffix=".html")
    raw.fetch(SHAPES, SDA, suffix=".zip")
    listing = raw.fetch(SOURCE, OPEN + "listeMunicipaliteMRC.json", suffix=".json")
    for item in json.loads(listing.path.read_text()):
        if item["type"] != "MRC":
            continue
        folder = f"{OPEN}MRC_{item['codeGeo']}/"
        events = raw.fetch(SOURCE, folder + "evenements.json", suffix=".json")
        for event in json.loads(events.path.read_text()):
            raw.fetch(SOURCE, f"{folder}{event['id']}.json", suffix=".json")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in elections())]


def parse() -> None:
    geo().write()
    registry().write()
    for name in elections():
        election(name).write()


def emit() -> None:
    package.run("load", packages())


@dataclass
class Candidate:
    name: str
    votes: int | None = None
    outcome: str | None = None
    incumbent: bool | None = None


@dataclass
class Post:
    mrc: str
    candidates: list[Candidate]
    electors: int | None = None
    valid: int | None = None
    rejected: int | None = None
    voters: int | None = None
    tacit: bool = False


@dataclass
class Vote:
    day: date
    kind: str
    posts: list[Post] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    source: str = MAMH


def _number(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


@functools.cache
def mrcs() -> dict[str, tuple[str, str]]:
    """Each MRC that elected a prefect, by folded name: its geographic code and its name."""
    listing = json.loads(raw.cached(SOURCE, OPEN + "listeMunicipaliteMRC.json").path.read_text())
    return {
        fold(item["nom"]): (str(item["codeGeo"]).zfill(2), item["nom"]) for item in listing if item["type"] == "MRC"
    }


def _json_votes() -> dict[str, Vote]:
    found: dict[str, Vote] = {}
    for _, (code, _name) in sorted(mrcs().items()):
        folder = f"{OPEN}MRC_{int(code)}/"
        events = json.loads(raw.cached(SOURCE, folder + "evenements.json").path.read_text())
        for event in events:
            link = f"{folder}{event['id']}.json"
            for record in json.loads(raw.cached(SOURCE, link).path.read_text()):
                if record["statutResultats"] not in ("FINAUX", "") or not record["postes"]:
                    continue
                day = date.fromisoformat(record["dateEvenement"])
                if day > date(2026, 11, 21):
                    continue
                label = f"{day}-{'general' if record['etendue'] == 'GEN' else 'by'}"
                vote = found.setdefault(
                    label,
                    Vote(day, "general" if record["etendue"] == "GEN" else "by_election", source=ELECTIONS_QUEBEC),
                )
                vote.urls.append(link)
                for poste in record["postes"]:
                    candidates = [
                        Candidate(
                            name=" ".join(
                                part for part in (c["prenomCandidat"].strip(), c["nomCandidat"].strip()) if part
                            ),
                            votes=_number(c["nombreVoteCandidat"]),
                            outcome=STATUS[c["statutCandidat"]],
                            incumbent=None if c["sortant"] not in ("SOR", "NON") else c["sortant"] == "SOR",
                        )
                        for c in poste["candidats"]
                    ]
                    tacit = any(c["statutCandidat"] == "ESO" for c in poste["candidats"])
                    if tacit:
                        for c in candidates:
                            c.votes = None
                    electors = _number(poste["nombreElecteurs"]) or None
                    vote.posts.append(
                        Post(
                            mrc=code,
                            candidates=candidates,
                            electors=None if tacit and not electors else electors,
                            valid=_number(poste["nombreVoteValides"]),
                            rejected=_number(poste["nombreVoteRejetes"]),
                            tacit=tacit,
                        )
                    )
    return found


def _text(year: int) -> list[str]:
    html = raw.cached(SOURCE, PAGES + PAGE[year]).path.read_text()
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)
    html = re.sub(r"<[^>]+>", "|", html).replace("&nbsp;", " ").replace("\xa0", " ")
    html = html.replace("&#039;", "'").replace("&#8217;", "’")
    return [" ".join(token.split()) for token in html.split("|") if token.strip()]


def _person(text: str) -> str:
    return re.sub(r"^(M\.|Mme\.?|Me)\s*", "", " ".join(text.split())).strip()


def _page(year: int) -> Vote:
    tokens = _text(year)
    names = {fold(name): name for name in (n for n, _ in ((v[1], 0) for v in mrcs().values()))}
    start = tokens.index("Dans cette page :") + 1
    stop = start + 1
    while fold(tokens[stop]) in names and fold(tokens[stop]) != fold(tokens[start]):
        stop += 1
    listed = [fold(token) for token in tokens[start:stop]]
    end = next(i for i, token in enumerate(tokens) if token.startswith("Dernière mise"))
    blocks: list[tuple[str, list[str]]] = []
    for token in tokens[stop:end]:
        if fold(token) in listed:
            blocks.append((fold(token), []))
        elif blocks:
            blocks[-1][1].append(token)
    if [name for name, _ in blocks] != listed:
        raise ValueError(f"{year}: the page lists {listed} but its sections are {[name for name, _ in blocks]}")
    vote = Vote(DAYS[year], "general", urls=[PAGES + PAGE[year]])
    for name, body in blocks:
        joined = " | ".join(body)

        def figure(pattern: str, joined: str = joined) -> int | None:
            found = re.search(pattern + r"\s*:\s*([\d ]+)", joined)
            return _number(found.group(1)) if found else None

        winner = re.search(r"Pr[ée]f[eè]te?\s*:\s*(?:\|\s*)?(.+?)\s*\|?\s*\(([^)]*)\)", joined)
        if winner is None:
            raise ValueError(f"{year} {name}: no prefect in {joined!r}")
        who, said = _person(winner.group(1).strip(" |")), fold(winner.group(2))
        tacit = "sans opposition" in said
        candidates: list[Candidate] = []
        listing = [i for i, t in enumerate(body) if t.startswith("Candidats")]
        for token in [] if tacit else body[listing[0] + 1 :] if listing else body:
            line = re.match(r"^(.*?),\s*([\d ]+) votes\b", token)
            if line:
                candidates.append(Candidate(_person(line.group(1)), _number(line.group(2))))
            elif year == DAYS[2005].year and re.match(r"^(M\.|Mme)", token):
                candidates.append(Candidate(_person(re.sub(r"\(retir[ée]e?\)", "", token))))
                if "retir" in token:
                    candidates[-1].outcome = "withdrew"
        if tacit:
            candidates = [Candidate(who, outcome="elected")]
        else:
            hits = [c for c in candidates if fold(c.name) == fold(who)]
            if len(hits) != 1:
                raise ValueError(f"{year} {name}: winner {who!r} not among {[c.name for c in candidates]}")
            for c in candidates:
                c.outcome = "elected" if c is hits[0] else c.outcome or "lost"
        code = next(code for key_, (code, _) in mrcs().items() if key_ == name)
        vote.posts.append(
            Post(
                mrc=code,
                candidates=candidates,
                electors=figure(r"lecteurs(?: inscrits)?"),
                valid=figure(r"Votes valides"),
                rejected=figure(r"Votes rejet[ée]s"),
                voters=figure(r"Votants"),
                tacit=tacit,
            )
        )
    return vote


@functools.cache
def elections() -> dict[str, Vote]:
    """Every vote by its name: `<year>` for the general elections and `<date>-by` for a by-election."""
    found = {str(year): _page(year) for year in PAGE}
    for label, vote in _json_votes().items():
        name = str(vote.day.year) if vote.kind == "general" else label
        found[name] = vote
    return dict(sorted(found.items()))


def used() -> dict[str, str]:
    """The MRCs that held a vote, by geographic code."""
    codes = {post.mrc for vote in elections().values() for post in vote.posts}
    return {code: name for code, name in sorted((c, n) for c, n in mrcs().values()) if code in codes}


def path(code: str) -> str:
    return f"ca/qc/mrc/{slug(used()[code])}"


def polity(code: str) -> str:
    return key("polity", code)


def area(code: str) -> str:
    return key("area", code)


@functools.cache
def _outlines() -> dict[str, shapely.Geometry]:
    archive = zipfile.ZipFile(raw.cached(SHAPES, SDA).path)
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read("Bdat/SHP/mrc_s.shp")),
        dbf=io.BytesIO(archive.read("Bdat/SHP/mrc_s.dbf")),
        encoding="utf8",
    )
    wanted = set(used())
    parts: defaultdict[str, list[shapely.Geometry]] = defaultdict(list)
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None or shape.shapeType == 0:
            continue
        code = str(record["MRS_CO_MRC"]).zfill(2)
        if code in wanted:
            parts[code].append(shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__)))
    found: dict[str, shapely.Geometry] = {}
    for code, shapes in parts.items():
        shape = shapely.simplify(shapely.union_all(shapes), 0.0003, preserve_topology=True)
        pieces = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
        found[code] = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(pieces))), 4326)
    return found


def _sources(p: Package) -> None:
    for page in PAGE.values():
        p.uses(raw.cached(SOURCE, PAGES + page))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The regional county municipalities of Quebec that elected a prefect by universal suffrage, each as a polity "
        "and an area, with outlines from the Ministry of Natural Resources and Forests",
    )
    p.external |= {CANADA, QUEBEC, QUEBEC_AREA}
    p.add(
        "source",
        id=MRNF,
        kind="official_body",
        publisher="Ministère des Ressources naturelles et des Forêts",
        title="Découpages administratifs 1/100 000",
        url=SDA_DATASET,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Gouvernement du Québec, Ministère des Ressources naturelles et des Forêts",
    )
    p.artifact(raw.cached(SHAPES, SDA), MRNF)
    p.add(
        "scheme",
        code="ca-qc.mrc",
        polity_id=QUEBEC,
        kind="administrative",
        rank=2,
        noun="regional county municipality",
        code_system="mamh",
    )
    retrieved = raw.cached(SHAPES, SDA).retrieved_at
    p.add(
        "boundary_set",
        id=key("boundary_set", "ca-qc.mrc", retrieved.year),
        scheme_code="ca-qc.mrc",
        vintage=str(retrieved.year),
        as_of=retrieved.date(),
    )
    shapes = _outlines()
    for code, text in used().items():
        place = area(code)
        p.add("area", id=place, scheme_code="ca-qc.mrc")
        p.add("area_code", area_id=place, code=code)
        p.add("area_parent", area_id=place, parent_id=QUEBEC_AREA)
        p.name(place, text, "fr", "official")
        if code in shapes:
            shape = shapes[code]
            p.add(
                "area_geometry",
                area_id=place,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=MRNF,
            )
        town = polity(code)
        p.add("polity", id=town, kind="county", iso_code=None)
        p.name(town, text, "fr", "official")
        p.add("polity_parent", polity_id=town, parent_id=QUEBEC)
        p.add("polity_territory", polity_id=town, area_id=place)
        p.add("slug", path=path(code), entity_id=town, is_current=True)
    return p


def _registered(p: Package) -> None:
    p.add(
        "source",
        id=MAMH,
        kind="official_body",
        publisher="Ministère des Affaires municipales et de l'Habitation",
        title="Résultats des élections municipales: préfet d'une MRC élu au suffrage universel",
        url=PAGES + PAGE[2017],
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Gouvernement du Québec, Ministère des Affaires municipales et de l'Habitation",
    )
    p.add(
        "source",
        id=ELECTIONS_QUEBEC,
        kind="official_body",
        publisher="Élections Québec",
        title="Données ouvertes: élections municipales",
        url=OPEN_PAGE,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Élections Québec",
    )


def _artifacts(p: Package, vote: Vote) -> None:
    for link in vote.urls:
        p.artifact(raw.cached(SOURCE, link), vote.source)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The prefect offices of Quebec's regional county municipalities and the electoral system of their votes",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CANADA, QUEBEC}
    _registered(p)
    _sources(p)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=QUEBEC,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(SYSTEM, "Quebec municipal plurality voting", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for code, text in used().items():
        office = key("office", code)
        p.add("office", id=office, polity_id=polity(code), kind="other")
        p.name(office, f"Prefect of the {text} regional county municipality", "en", "official")
        p.name(office, f"Préfet de la MRC {text}", "fr", "native", False)
    return p


def election(name: str) -> Package:
    vote = elections()[name]
    day = vote.day
    p = Package(
        f"{SOURCE}/{name}",
        SOURCE,
        f"The {day} Quebec MRC prefect "
        + ("elections by universal suffrage" if vote.kind == "general" else "by-election by universal suffrage"),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CANADA, QUEBEC}
    _artifacts(p, vote)
    kind = "local" if vote.kind == "general" else "by_election"
    when = day.isoformat()
    umbrella = key("election", name)
    p.add("election", id=umbrella, polity_id=QUEBEC, kind=kind, date_from=day, date_to=day, status="held")
    label = "Quebec MRC prefect elections" if vote.kind == "general" else "Quebec MRC prefect by-election"
    p.name(umbrella, f"{day.year} {label}" if vote.kind == "general" else f"{label} of {when}", "en", "official")
    p.name(
        umbrella,
        f"Élections des préfets de MRC au suffrage universel de {day.year}"
        if vote.kind == "general"
        else f"Élection partielle du préfet de MRC du {when}",
        "fr",
        "native",
        False,
    )
    p.add("slug", path=f"ca/qc/mrc/{when}-prefect", entity_id=umbrella, is_current=True)
    close = day + timedelta(days=1)
    for post in sorted(vote.posts, key=lambda post: post.mrc):
        code, text = post.mrc, used()[post.mrc]
        local = key("election", name, code)
        p.add(
            "election",
            id=local,
            polity_id=polity(code),
            parent_id=umbrella,
            kind=kind,
            date_from=day,
            date_to=day,
            status="held",
        )
        p.name(
            local,
            f"{day.year} {text} prefect election" if vote.kind == "general" else f"{when} {text} prefect by-election",
            "en",
            "official",
        )
        p.add("slug", path=f"{path(code)}/{when}-prefect", entity_id=local, is_current=True)
        unit = key("unit", name, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CANADA,
            scheme_code="ca-qc.mrc",
            code=f"{when}:{code}",
            area_relation="equals",
            area_id=area(code),
            kind="standard",
            valid_from=day,
            valid_to=close,
        )
        contest = key("contest", name, code)
        p.add(
            "contest",
            id=contest,
            election_id=local,
            kind="office",
            office_id=key("office", code),
            body_id=None,
            district_area_id=area(code),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="tacit" if post.tacit else "counted",
        )
        p.name(contest, f"{text}: Prefect", "en", "official")
        p.add("slug", path=f"{path(code)}/{when}-prefect/prefect", entity_id=contest, is_current=True)
        result_set = key("result_set", name, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=local,
            contest_id=contest,
            stage="official",
            kind="tacit" if post.tacit else "counted",
            source_id=vote.source,
            status="active",
            is_default=True,
            completeness=None if post.tacit else 1.0,
        )
        if not post.tacit:
            for measure, figure in ((2, post.electors), (3, post.voters), (4, post.valid), (5, post.rejected)):
                if figure is not None:
                    _value(p, result_set, unit, None, measure, figure)
        for number, candidate in enumerate(post.candidates, start=1):
            seq = f"{code}:{number}"
            person = key("person", name, seq)
            p.add("person", id=person, birth_precision=None)
            p.name(person, candidate.name, "fr", "official")
            option = key("option", name, seq)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None,
                ballot_position=number,
                ballot_label=candidate.name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", name, seq),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
                member_of_party_id=None,
                is_independent=None,
                is_incumbent=candidate.incumbent,
            )
            if candidate.votes is not None and not post.tacit:
                _value(p, result_set, unit, option, 1, candidate.votes)
            if candidate.outcome is not None:
                p.add("outcome", result_set_id=result_set, option_id=option, kind=candidate.outcome)
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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
