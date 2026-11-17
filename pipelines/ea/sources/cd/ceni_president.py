"""The 2023 presidential election of the Democratic Republic of the Congo, by polling station, from the CENI results portal.

The portal answers one JSON request per polling station, so the crawl stores each province's responses as one
consolidated file in the raw cache, keyed by the province's listing URL plus ``#bureaux``.

Provinces, circonscriptions, and Kinshasa's communes are the areas of ``cd.ceni/geo``. The portal also lists three
territories that the 2023 deputy elections did not reach (Masi-Manimba, Yakoma, and Bomongo), so their areas and
outlines are added here in the same scheme.
"""

import functools
import json
import re
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from uuid import uuid5

import httpx
import shapely

from ea import package, raw
from ea.package import Package
from ea.sources.cd import ceni
from ea.store import DATA

SOURCE = "cd.ceni_president"
API = "https://bosolo.ceni.cd/e20231220/bosolo/mapapi.php"
PROVINCES = f"{API}?data=provinces"
NATIONAL = f"{API}?p=0"
WORKERS = 6
ATTEMPTS = 5


def listing_url(province: str) -> str:
    return f"{API}?c={province}"


def crawl_url(province: str) -> str:
    return f"{API}?c={province}#bureaux"


def results_url(site: str, bureau: str) -> str:
    return f"{API}?value={site}_{bureau}&type=pres&circ=bv"


def province_url(province: str) -> str:
    return f"{API}?value={province}&type=pres&circ=province"


def circonscription_url(circonscription: str) -> str:
    return f"{API}?value={circonscription}&type=pres&circ=secteur"


def read(name: str) -> list[dict[str, str]]:
    return json.loads(raw.cached(SOURCE, name).path.read_text("utf-8"))


def stations(province: str) -> Iterator[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    for row in read(listing_url(province)):
        pair = (row["site_vote_id"], row["bv_id"])
        if pair not in seen:
            seen.add(pair)
            yield pair


def fetch() -> None:
    ceni.fetch()
    raw.fetch(SOURCE, NATIONAL, suffix=".json")
    raw.fetch(SOURCE, PROVINCES, suffix=".json")
    provinces = [row["province_id"] for row in read(PROVINCES)]
    for province in provinces:
        raw.fetch(SOURCE, listing_url(province), suffix=".json")
        raw.fetch(SOURCE, province_url(province), suffix=".json")
    for province in provinces:
        for circonscription in sorted({row["circonscription_id"] for row in read(listing_url(province))}):
            raw.fetch(SOURCE, circonscription_url(circonscription), suffix=".json")
    client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, timeout=120)
    folder = DATA / "raw" / SOURCE / ".crawl"
    folder.mkdir(parents=True, exist_ok=True)

    def one(pair: tuple[str, str]) -> tuple[str, str, list[object]]:
        site, bureau = pair
        for attempt in range(ATTEMPTS):
            try:
                response = client.get(results_url(site, bureau))
                response.raise_for_status()
                rows = json.loads(response.text)
            except httpx.HTTPError, ValueError:
                raw_sleep(attempt)
                continue
            if isinstance(rows, list):
                return site, bureau, rows
            raw_sleep(attempt)
        raise RuntimeError(f"{results_url(site, bureau)} did not answer with a JSON list")

    with client, ThreadPoolExecutor(WORKERS) as pool:
        for province in provinces:
            if (crawl_url(province)) in raw.keys(SOURCE):
                continue
            partial = folder / f"{province}.jsonl"
            done: dict[tuple[str, str], list[object]] = {}
            if partial.exists():
                for line in partial.read_text().splitlines():
                    site, bureau, rows = json.loads(line)
                    done[(site, bureau)] = rows
            todo = [pair for pair in stations(province) if pair not in done]
            total = len(todo) + len(done)
            with partial.open("a") as out:
                for start in range(0, len(todo), 500):
                    for site, bureau, rows in pool.map(one, todo[start : start + 500]):
                        done[(site, bureau)] = rows
                        out.write(json.dumps([site, bureau, rows]) + "\n")
                    out.flush()
                    print(f"{SOURCE}: province {province}: {len(done)} of {total}", flush=True)
            body = {f"{site}_{bureau}": done[(site, bureau)] for site, bureau in stations(province)}
            raw.store(
                SOURCE,
                crawl_url(province),
                json.dumps(body, separators=(",", ":")).encode(),
                suffix=".json",
                retrieved_at=datetime.now(UTC),
                media_type="application/json",
            )
            partial.unlink()


def raw_sleep(attempt: int) -> None:
    from time import sleep

    print(f"{SOURCE}: retrying after a failed request", file=sys.stderr, flush=True)
    sleep(2**attempt)


DAY = date(2023, 12, 20)
CONGO = ceni.CONGO
TERRITORY = ceni.COUNTRY
BLANK = "1_1_1_999_999"
INDEPENDENT = "INDEPENDANT"
SITE, BUREAU = "cd.site", "cd.bureau"
# The portal's province numbers outside the country: its five pilot countries for voting abroad.
ABROAD = {"27", "28", "29", "30", "31"}
METHOD = (
    "Counted by the Commission électorale nationale indépendante per polling station (bureau de vote) and published "
    "on its results portal. A polling station is identified by its polling site (site de vote) and a letter. The "
    "portal gives each station's votes for every candidate and its blank votes, and no registered voters, ballots "
    "cast, or invalid ballots, so only those are stored. The portal's percentages count blank votes in the base. "
    "Voting abroad, in five pilot countries, is reported by embassy polling station and is not placed on a map. "
    "The portal lists no polling station in the territory of Basankusu (Équateur)."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CENI_SOURCE = key("source", SOURCE)
PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president", 2011)
PLAN_FROM = date(2023, 6, 15)


def pretty(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().title()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/pres-2023"]


def parse() -> None:
    geo().write()
    registry().write()
    president().write()


def emit() -> None:
    package.run("load", packages())


def provinces() -> list[dict[str, str]]:
    return sorted(read(PROVINCES), key=lambda row: int(row["province_id"]))


@functools.cache
def places() -> dict[str, ceni.Circ]:
    """The circonscription, or in Kinshasa the commune, of each of the portal's circonscription numbers."""
    out: dict[str, ceni.Circ] = {}
    for province in provinces():
        if province["province_id"] in ABROAD:
            continue
        if province["province"] not in ceni.PROVINCES:
            raise ValueError(f"{SOURCE}: unknown province {province['province']!r}")
        for row in read(listing_url(province["province_id"])):
            out.setdefault(row["circonscription_id"], ceni._circ(province["province"], row["circonscription"]))
    return out


@functools.cache
def added() -> list[ceni.Circ]:
    """The portal's circonscriptions that ``cd.ceni/geo`` lacks."""
    known = {c.code for c in ceni.circs().values()}
    return sorted({c.code: c for c in places().values() if c.code not in known}.values(), key=lambda c: c.code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The polling sites and stations of the 2023 elections in the Democratic Republic of the Congo, and the three "
        "circonscriptions of the presidential vote that the deputy elections of 2023 did not reach",
        depends=["cd.ceni/geo"],
    )
    p.external |= {CONGO, ceni.GB_ADM2}
    territories = raw.cached(ceni.SOURCE, ceni.GB_TERRITORIES)
    p.uses(territories)
    p.add("scheme", code=SITE, polity_id=CONGO, kind="precinct", rank=4, noun="polling site")
    p.add("scheme", code=BUREAU, polity_id=CONGO, kind="precinct", rank=5, noun="polling station")
    features = ceni._features(ceni.GB_TERRITORIES)
    for c in added():
        id = key("area", ceni.SCHEME, c.code)
        p.external.add(c.parent)
        p.add("area", id=id, scheme_code=ceni.SCHEME)
        p.add("area_code", area_id=id, code=c.code)
        p.add("area_parent", area_id=id, parent_id=c.parent)
        p.name(id, c.name, "fr", "official")
        p.add(
            "reporting_unit",
            id=key("unit", ceni.SCHEME, c.code),
            polity_id=CONGO,
            scheme_code=ceni.SCHEME,
            code=c.code,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
        shape = ceni._shape(features[ceni.fold(c.base)])
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=PLAN_FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=ceni.GB_ADM2,
        )
    return p


def placed(circonscription: str) -> tuple[str, str]:
    """The area and the reporting unit of a portal circonscription."""
    c = places()[circonscription]
    if c in added():
        return key("area", c.scheme, c.code), key("unit", c.scheme, c.code)
    return ceni.area(c.scheme, c.code), ceni.unit(c.scheme, c.code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The presidency of the Democratic Republic of the Congo, its plurality electoral system, and the parties of "
        "the 2023 presidential candidates",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CONGO, TERRITORY}
    p.add(
        "source",
        id=CENI_SOURCE,
        kind="official_body",
        publisher="Commission électorale nationale indépendante",
        title="CENI Bosolo: résultats des élections du 20 décembre 2023",
        url="https://bosolo.ceni.cd/e20231220/bosolo/",
        license="Published by the electoral commission without a stated license",
        attribution="Commission électorale nationale indépendante (CENI)",
    )
    for artifact in sources():
        p.artifact(artifact, CENI_SOURCE)
    p.add("office", id=PRESIDENCY, polity_id=CONGO, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Democratic Republic of the Congo", "en", "official")
    p.name(PRESIDENCY, "Président de la République démocratique du Congo", "fr", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=CONGO,
        valid_from=date(2011, 1, 20),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Since the constitutional revision of 20 January 2011, the President is elected by the direct vote of the "
            "whole country in a single round, and the candidate with the most votes wins."
        ),
        lang="en",
    )
    for sigle in sorted(parties()):
        id = key("party", sigle)
        p.add("party", id=id, kind="party", polity_id=CONGO)
        p.name(id, sigle, "fr", "official")
    return p


def candidates() -> list[dict[str, str]]:
    return sorted(read(NATIONAL), key=lambda row: int(row["ordre_bulletin"] or 0))


def parties() -> set[str]:
    return {row["sigle"] for row in candidates() if row["candidat_id"] != BLANK and row["sigle"] != INDEPENDENT}


def sources() -> list[raw.Artifact]:
    found = [raw.cached(SOURCE, NATIONAL), raw.cached(SOURCE, PROVINCES)]
    for province in provinces():
        found += [
            raw.cached(SOURCE, listing_url(province["province_id"])),
            raw.cached(SOURCE, crawl_url(province["province_id"])),
        ]
        found.append(raw.cached(SOURCE, province_url(province["province_id"])))
    return found


def person_name(row: dict[str, str]) -> str:
    return pretty(row["candidat"])


def _crawl(province: str) -> dict[str, list[dict[str, str]]]:
    return json.loads(raw.cached(SOURCE, crawl_url(province)).path.read_text("utf-8"))


def president() -> Package:
    p = Package(
        f"{SOURCE}/pres-2023",
        SOURCE,
        "The 20 December 2023 presidential election of the Democratic Republic of the Congo, by polling station",
        depends=["cd.ceni/geo", f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CONGO, TERRITORY}
    for artifact in sources():
        p.uses(artifact)
    national = candidates()
    election = key("election", "pres", 2023)
    p.add("election", id=election, polity_id=CONGO, kind="general", date_from=DAY, date_to=DAY, status="held")
    path = f"cd/{DAY}-presidential"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "2023 Congolese presidential election", "en", "official")
    p.name(election, "Élection présidentielle de 2023", "fr", "native", False)
    contest = key("contest", "pres", 2023)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=TERRITORY,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, "2023 Congolese presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", "pres", 2023)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CENI_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    for row in national:
        if row["candidat_id"] == BLANK:
            continue
        person = key("person", row["candidat_id"])
        p.add("person", id=person, gender={"M": "male", "F": "female"}.get(row["sexe"] or ""))
        p.name(person, person_name(row), "fr", "official")
        option = options[row["candidat_id"]] = key("option", "pres", 2023, row["candidat_id"])
        independent = row["sigle"] == INDEPENDENT
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=None if independent else key("party", row["sigle"]),
            ballot_position=int(row["ordre_bulletin"]),
            ballot_label=person_name(row),
        )
        p.add(
            "candidacy",
            id=key("candidacy", "pres", 2023, row["candidat_id"]),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=person_name(row),
            member_of_party_id=None if independent else key("party", row["sigle"]),
            is_independent=independent,
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="elected" if row["voixliste"] is not None else "lost",
        )
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=int(row["voix"]))
    blank = next(row for row in national if row["candidat_id"] == BLANK)
    p.add("declared", result_set_id=result_set, option_id=None, measure=6, value=int(blank["voix"]))
    sites: set[str] = set()
    minted = {key("area", c.scheme, c.code) for c in added()}
    names = {
        (row["site_vote_id"]): (row["site_vote"], row["circonscription_id"], row["province_id"])
        for province in provinces()
        for row in read(listing_url(province["province_id"]))
    }
    for province in provinces():
        abroad = province["province_id"] in ABROAD
        for station, rows in _crawl(province["province_id"]).items():
            site, letter = station.split("_")
            _, circ, _ = names[site]
            if abroad:
                relation, place, parent, kind = "unlocated", None, None, "abroad_mission"
            else:
                place, parent = placed(circ)
                relation, kind = "within", "standard"
                if place not in minted:
                    p.external |= {place, parent}
            if site not in sites:
                sites.add(site)
                p.add(
                    "reporting_unit",
                    id=key("unit", "site", site),
                    polity_id=CONGO,
                    scheme_code=SITE,
                    code=f"{DAY}:{site}",
                    parent_id=parent,
                    area_relation=relation,
                    area_id=place,
                    kind="aggregate" if not abroad else "abroad_mission",
                )
            unit = key("unit", "bureau", station)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CONGO,
                scheme_code=BUREAU,
                code=f"{DAY}:{site}:{letter}",
                parent_id=key("unit", "site", site),
                area_relation=relation,
                area_id=place,
                kind=kind,
            )
            seen: set[str] = set()
            for row in rows:
                seen.add(row["candidat_id"])
                option = options.get(row["candidat_id"])
                if row["candidat_id"] != BLANK and option is None:
                    raise ValueError(f"{station}: candidate {row['candidat_id']} is not on the national list")
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1 if option else 6,
                    count_round=0,
                    flags=0,
                    value=int(row["voix"]),
                )
    return p
