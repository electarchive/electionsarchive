import json
import re
import time
import unicodedata
from collections import Counter
from datetime import date, timedelta
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "eu.europarl"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
API = "https://data.europarl.europa.eu/api/v2"
FORMAT = "format=application%2Fld%2Bjson"
TERM = 10
COUNTRIES = ("DE", "IE")
WINDOW = date(2024, 1, 1)
GROUP = "def/ep-entities/EU_POLITICAL_GROUP"
NATIONAL = "def/ep-entities/NATIONAL_POLITICAL_GROUP"
SEATED = (f"org/ep-{TERM}", "def/ep-roles/MEMBER_PARLIAMENT")
PARTIES = {
    "CDU": "CDU",
    "SPD": "SPD",
    "GRÜNE.": "GRÜNE",
    "FDP": "FDP",
    "DIE LINKE.": "DIE LINKE",
    "CSU": "CSU",
    "Familien-Partei DE": "FAMILIE",
    "ÖDP": "ÖDP",
    "AfD": "AFD",
    "Die PARTEI": "DIE PARTEI",
    "Freie Wähler": "FREIE WÄHLER",
    "Die Tierschutzpartei": "TIERSCHUTZPARTEI",
    "Bündnis Sahra": "BSW",
    "BSW": "BSW",
    "V": "VOLT",
    "PdF": "PDF",
    "SF": "SF",
    "FF": "FF",
    "FG": "FG",
    "LABOUR": "LAB",
    "Independent": None,
    "Ind.": None,
}
ABBREVIATIONS = {"PPE": "EPP", "Verts/ALE": "Greens/EFA"}
GENDERS = {"MALE": "male", "FEMALE": "female"}
DECLINED = "{name} was elected from this list but did not take up the seat, so this member took it instead."


def listing(country: str) -> str:
    return f"{API}/meps?parliamentary-term={TERM}&country-of-representation={country}&{FORMAT}&offset=0&limit=500"


def get(url: str) -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        time.sleep(1)
        return raw.fetch(SOURCE, url, suffix=".json")


def tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.replace("ß", "ss")).encode("ascii", "ignore").decode().upper()
    return [token for token in (re.sub(r"[^A-Z0-9]", "", part) for part in re.split(r"[\s-]+", folded)) if token]


def fetch() -> None:
    organizations: set[str] = set()
    for country in COUNTRIES:
        for mep in json.loads(get(listing(country)).path.read_text())["data"]:
            detail = json.loads(get(f"{API}/meps/{mep['identifier']}?{FORMAT}").path.read_text())["data"][0]
            organizations |= {
                m["organization"]
                for m in detail["hasMembership"]
                if m.get("membershipClassification") in (GROUP, NATIONAL)
                and (m["memberDuring"].get("endDate") or "9999") >= WINDOW.isoformat()
            }
    for organization in sorted(organizations):
        get(f"{API}/corporate-bodies/{organization.split('/')[1]}?{FORMAT}")


def parse() -> None:
    urls: list[str] = []
    meps: list[dict[str, object]] = []
    held: dict[tuple[str, str, str], list[tuple[date, date, str | None]]] = {}
    bodies: dict[str, dict] = {}
    for country in COUNTRIES:
        urls.append(listing(country))
        for listed in json.loads(raw.cached(SOURCE, listing(country)).path.read_text())["data"]:
            urls.append(f"{API}/meps/{listed['identifier']}?{FORMAT}")
            detail = json.loads(raw.cached(SOURCE, urls[-1]).path.read_text())["data"][0]
            seats = [
                m["memberDuring"] for m in detail["hasMembership"] if (m.get("organization"), m.get("role")) == SEATED
            ]
            if len(seats) != 1:
                raise ValueError(f"MEP {detail['identifier']} has {len(seats)} term {TERM} seats")
            meps.append(
                {
                    "country": country,
                    "mep": detail["identifier"],
                    "given": detail["givenName"],
                    "family": detail["familyName"],
                    "birth": date.fromisoformat(detail["bday"]) if detail.get("bday") else None,
                    "gender": GENDERS.get((detail.get("hasGender") or "").rsplit("/", 1)[-1]),
                    "start": date.fromisoformat(seats[0]["startDate"]),
                    "end": date.fromisoformat(seats[0]["endDate"]) if seats[0].get("endDate") else None,
                }
            )
            for m in detail["hasMembership"]:
                period = m["memberDuring"]
                if (
                    m.get("membershipClassification") not in (GROUP, NATIONAL)
                    or (period.get("endDate") or "9999") < WINDOW.isoformat()
                ):
                    continue
                if m["organization"] not in bodies:
                    urls.append(f"{API}/corporate-bodies/{m['organization'].split('/')[1]}?{FORMAT}")
                    bodies[m["organization"]] = json.loads(raw.cached(SOURCE, urls[-1]).path.read_text())["data"][0]
                body = bodies[m["organization"]]
                if body["classification"] == NATIONAL and body["label"] not in PARTIES:
                    raise ValueError(f"unknown national party {body['label']!r} ({m['organization']})")
                end = period.get("endDate")
                held.setdefault(
                    (
                        country,
                        detail["identifier"],
                        "parliamentary_group" if body["classification"] == GROUP else "party",
                    ),
                    [],
                ).append(
                    (
                        date.fromisoformat(period["startDate"]),
                        date.fromisoformat(end) + timedelta(days=1) if end else date.max,
                        body["label"] if body["classification"] == GROUP else PARTIES[body["label"]],
                    )
                )
    spans: list[tuple[str, str, str, date, date | None, str | None]] = []
    for (country, mep, kind), periods in held.items():
        merged: list[tuple[date, date, str | None]] = []
        for low, high, value in sorted(periods, key=lambda period: period[0]):
            if merged and low <= merged[-1][1] and merged[-1][2] == value:
                merged[-1] = (merged[-1][0], max(merged[-1][1], high), value)
            elif merged and low < merged[-1][1]:
                raise ValueError(f"MEP {mep} holds two {kind} memberships at once from {low}")
            else:
                merged.append((low, high, value))
        spans.extend(
            (country, mep, kind, low, None if high == date.max else high, value) for low, high, value in merged
        )
    names: dict[str, list[tuple[date, date | None, str]]] = {}
    for body in sorted(bodies.values(), key=lambda body: body["temporal"]["startDate"]):
        if body["classification"] != GROUP:
            continue
        start = date.fromisoformat(body["temporal"]["startDate"])
        end = body["temporal"].get("endDate")
        stop = date.fromisoformat(end) + timedelta(days=1) if end else None
        history = names.setdefault(body["label"], [])
        if history and history[-1][2] == body["prefLabel"]["en"] and history[-1][1] == start:
            history[-1] = (history[-1][0], stop, history[-1][2])
        else:
            history.append((start, stop, body["prefLabel"]["en"]))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"url": urls}).write_parquet(PARSED / "urls.parquet")
    pl.DataFrame(meps).write_parquet(PARSED / "meps.parquet")
    pl.DataFrame(spans, schema=["country", "mep", "kind", "start", "stop", "value"], orient="row").write_parquet(
        PARSED / "spans.parquet"
    )
    pl.DataFrame(
        [(label, *span) for label, history in names.items() for span in history],
        schema=["label", "start", "stop", "name"],
        orient="row",
    ).write_parquet(PARSED / "groups.parquet")


def emit() -> Path:
    meps = pl.read_parquet(PARSED / "meps.parquet").sort("start", "mep")
    spans = pl.read_parquet(PARSED / "spans.parquet")
    groups = pl.read_parquet(PARSED / "groups.parquet")
    held: dict[str, list[tuple[str, date, date, str | None]]] = {}
    for _, mep, kind, start, stop, value in spans.iter_rows():
        held.setdefault(mep, []).append((kind, start, stop or date.max, value))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Members of the European Parliament from Germany and Ireland in the 2024–2029 term, with their "
            "national parties and political groups",
            polity="EU",
        )
        title = "Open Data Portal: Members of the European Parliament and their memberships"
        source = cs.source("European Parliament", title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="European Parliament",
            title=title,
            url="https://data.europarl.europa.eu",
            license="European Parliament legal notice: reuse is authorized provided the source is acknowledged",
            license_url="https://www.europarl.europa.eu/legal-notice/en/",
            attribution="© European Union – Source: European Parliament",
        )
        for url in pl.read_parquet(PARSED / "urls.parquet")["url"]:
            cs.artifact(raw.cached(SOURCE, url), source)

        union = cs.polity("EU")
        parliament = cs.key("eu.body", "european-parliament")
        if {union, parliament} & cs.minted:
            raise ValueError("the European Union and its Parliament come from eu.eurlex; apply it first")
        parties = {
            (country, code): cs.key(f"{country.lower()}.party", code)
            for country, code in spans.filter(pl.col("kind") == "party", pl.col("value").is_not_null())
            .select("country", "value")
            .unique()
            .iter_rows()
        }
        if unknown := [code for code, id in parties.items() if id in cs.minted]:
            raise ValueError(f"national parties missing from the archive: {unknown}")
        affiliates: dict[str, UUID] = {}
        for label, start, stop, text in groups.iter_rows():
            if label not in affiliates:
                affiliates[label] = cs.key("ep.group", label)
                cs.add("party", id=affiliates[label], polity_id=union, kind="parliamentary_group")
                cs.add(
                    "name",
                    entity_id=affiliates[label],
                    text=ABBREVIATIONS.get(label, label),
                    lang="en",
                    kind="abbreviation",
                    is_preferred=False,
                )
            cs.add(
                "name",
                entity_id=affiliates[label],
                text=text,
                lang="en",
                kind="official",
                is_preferred=stop is None,
                valid_from=start,
                valid_to=stop,
            )

        pool = [
            (
                scheme.split(".")[0].upper(),
                person,
                contest,
                party,
                text,
                set(tokens(text)),
                year,
            )
            for scheme, person, contest, party, text, year in conn.execute(
                """
                select x.scheme, c.person_id, o.contest_id, coalesce(c.member_of_party_id, o.party_id),
                       (select n.text from name n where n.entity_id = c.person_id and n.is_preferred
                        order by n.lang limit 1),
                       extract(year from p.birth_date)::int
                from external_id x
                join contest ct on ct.election_id = x.entity_id
                join contest_option o on o.contest_id = ct.id
                join candidacy c on c.option_id = o.id
                join person p on p.id = c.person_id
                where (x.scheme, x.value) in (('de.election', '2024-06-09:european-parliament'),
                                              ('ie.election', '2024-06-07:european-parliament'))
                  and (c.role = 'list_member'
                       or exists (select 1 from outcome u where u.option_id = o.id and u.kind = 'elected'))
                """
            ).fetchall()
        ]
        matched: dict[str, tuple[UUID, UUID, UUID | None]] = {}
        for country, mep, given, family, birth, *_ in meps.iter_rows():
            surnames = {token for token in tokens(family) if len(token) > 3} or set(tokens(family))
            first = tokens(given)[0]
            found = [
                entry
                for entry in pool
                if entry[0] == country
                and entry[5] & surnames
                and (
                    any(SequenceMatcher(None, first, token).ratio() >= 0.6 for token in entry[5] - surnames)
                    or (birth is not None and entry[6] == birth.year)
                )
            ]
            if len(found) > 1:
                raise ValueError(f"MEP {mep} ({given} {family}) matches {[entry[4] for entry in found]}")
            if found:
                pool.remove(found[0])
                matched[mep] = (found[0][1], found[0][2], found[0][3])

        tenures: list[tuple[UUID, str, UUID | None, date, UUID, str, date]] = []
        staged: list[dict[str, object]] = []
        succeeded: set[UUID] = set()
        for country, mep, given, family, birth, gender, start, end in meps.iter_rows():
            stop = end + timedelta(days=1) if end else date.max
            predecessor = None
            declined = None
            if mep in matched:
                person, contest, list_party = matched[mep]
                cs.add("external_id", entity_id=person, scheme="ep.mep", value=mep)
            else:
                list_party = next(
                    parties.get((country, value or ""))
                    for kind, low, high, value in held[mep]
                    if kind == "party" and low <= start < high
                )
                before = max(
                    (
                        tenure
                        for tenure in tenures
                        if tenure[1] == country
                        and tenure[2] == list_party
                        and tenure[3] <= start
                        and tenure[0] not in succeeded
                    ),
                    key=lambda tenure: tenure[3],
                    default=None,
                )
                if before is not None:
                    predecessor, contest = before[0], before[4]
                    succeeded.add(predecessor)
                else:
                    declined = next(entry for entry in pool if entry[0] == country and entry[3] == list_party)
                    pool.remove(declined)
                    contest = declined[2]
                person = cs.key("ep.mep", mep)
                if person in cs.minted:
                    cs.add(
                        "person",
                        id=person,
                        birth_date=birth,
                        birth_precision="day" if birth else None,
                        gender=gender,
                    )
                    cs.add(
                        "name",
                        entity_id=person,
                        text=f"{given} {family}",
                        lang=country.lower() if country == "DE" else "en",
                        kind="official",
                        is_preferred=True,
                    )
            tenure = cs.key(f"{SOURCE}.tenure", f"{TERM}:{mep}")
            staged.append(
                {
                    "id": tenure,
                    "person_id": person,
                    "body_id": parliament,
                    "from_date": start,
                    "to_date": end,
                    "acquired_via": "elected" if predecessor is None else "list_replacement",
                    "contest_id": contest,
                    "succeeds_tenure_id": predecessor,
                }
            )
            if declined is not None:
                cs.add("note", entity_id=tenure, kind="context", body_md=DECLINED.format(name=declined[4]))
            tenures.append((tenure, country, list_party, stop, contest, mep, start))
        for row in staged:
            cs.add("tenure", **row)
        for tenure, country, _, stop, _, mep, start in tenures:
            for kind, low, high, value in held[mep]:
                if max(low, start) < min(high, stop):
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind=kind,
                        party_id=affiliates[value or ""]
                        if kind == "parliamentary_group"
                        else parties.get((country, value or "")),
                        valid_from=max(low, start),
                        valid_to=None if min(high, stop) == date.max else min(high, stop),
                    )

        joint: dict[UUID, list[tuple[date, date, UUID]]] = {}
        for country, mep, *_ in meps.iter_rows():
            for kind, low, high, value in held[mep]:
                for other, first, last, label in held[mep]:
                    if (
                        kind == "party"
                        and value is not None
                        and other == "parliamentary_group"
                        and max(low, first, WINDOW) < min(high, last)
                    ):
                        joint.setdefault(parties[country, value], []).append(
                            (max(low, first, WINDOW), min(high, last), affiliates[label or ""])
                        )
        for party, overlaps in joint.items():
            segments: list[tuple[date, date, UUID]] = []
            for low, high in pairwise(sorted({day for first, last, _ in overlaps for day in (first, last)})):
                tally = Counter(group for first, last, group in overlaps if first <= low and high <= last)
                if not tally:
                    continue
                group = tally.most_common(1)[0][0]
                if segments and segments[-1][2] == group:
                    segments[-1] = (segments[-1][0], high, group)
                else:
                    segments.append((low, high, group))
            for low, high, group in segments:
                cs.add(
                    "party_affiliation",
                    party_id=party,
                    parent_party_id=group,
                    kind="ep_group",
                    valid_from=low,
                    valid_from_precision="unknown" if low == WINDOW else "day",
                    valid_to=None if high == date.max else high,
                )
        print(
            "MEP tenures:",
            len(tenures),
            "list replacements:",
            len(succeeded),
            "elected who never sat:",
            [entry[4] for entry in pool],
        )
        return cs.write()
