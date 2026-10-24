import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.msp_wikidata import fold, seat
from ea.store import DATA, connect

SOURCE = "gb.devolved_tenures"
VERSION = "1"
TITLE = "Members of the Scottish Parliament and the Senedd (P39) on Wikidata"
BODIES = {
    "Scottish Parliament": ("Q1711695", r"the (\d\w\w) Scottish"),
    "Senedd Cymru": ("Q3406079", r"the (\d\w\w) (?:National Assembly for Wales|Senedd)"),
}
TERMS = {"1st": 1999, "2nd": 2003, "3rd": 2007, "4th": 2011, "5th": 2016, "6th": 2021}
UNMATCHED = DATA / "tmp" / "uk-dev" / "devolved_tenures_unmatched.tsv"


def service(item: str) -> str:
    return "https://query.wikidata.org/sparql?" + urlencode(
        {
            "query": f"""SELECT ?item ?name ?posLabel ?start ?end ?causeLabel ?districtLabel WHERE {{
?pos wdt:P279* wd:{item} . ?item p:P39 ?held . ?held ps:P39 ?pos .
OPTIONAL {{ ?held pq:P580 ?start }} OPTIONAL {{ ?held pq:P582 ?end }} OPTIONAL {{ ?held pq:P1534 ?cause }}
OPTIONAL {{ ?held pq:P768 ?district }} OPTIONAL {{ ?item rdfs:label ?name FILTER(LANG(?name) = "en") }}
SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}""",
            "format": "json",
        }
    )


def day(value: dict[str, str] | None) -> date | None:
    return date.fromisoformat(value["value"][:10]) if value is not None else None


def fetch() -> None:
    for item, _ in BODIES.values():
        raw.fetch(SOURCE, service(item), suffix=".json")


def ended(cause: str) -> str | None:
    text = cause.lower()
    for needle, result in (("resign", "resigned"), ("death", "died"), ("died", "died"), ("disqualif", "disqualified"), ("expel", "expelled")):
        if needle in text:
            return result
    return None


def emit() -> Path:
    live = "not exists (select from entity_merge m where m.entity_id = x.entity_id)"
    with connect() as conn:
        held: dict[str, set[UUID]] = defaultdict(set)
        owned: dict[UUID, set[str]] = defaultdict(set)
        for person, value in conn.execute(
            f"select x.entity_id, x.value from external_id x join person p on p.id = x.entity_id where x.scheme = 'wikidata' and {live}"
        ).fetchall():
            held[value].add(person)
            owned[person].add(value)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish Parliament and Senedd members of 1999 to 2021 with tenures from the elected candidacies and Wikidata's term dates",
            polity="GB",
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Property:P39",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for item, _ in BODIES.values():
            cs.artifact(raw.cached(SOURCE, service(item)), source)
        unmatched: list[tuple[str, str]] = []
        totals = Counter()
        samples = []
        for body_name, (item, pattern) in BODIES.items():
            body = conn.execute(
                "select b.id from body b join name n on n.entity_id = b.id and n.is_preferred where n.text = %s", [body_name]
            ).fetchone()[0]
            records: dict[tuple[int, str], list[dict[str, object]]] = defaultdict(list)
            labels: dict[str, str] = {}
            names: dict[tuple[int, str], set[tuple[str, str]]] = defaultdict(set)
            starts: dict[int, Counter] = defaultdict(Counter)
            ends: dict[int, Counter] = defaultdict(Counter)
            for binding in json.loads(raw.cached(SOURCE, service(item)).path.read_text(encoding="utf-8"))["results"]["bindings"]:
                term = re.search(pattern, binding["posLabel"]["value"])
                if term is None or term[1] not in TERMS:
                    continue
                year = TERMS[term[1]]
                qid = binding["item"]["value"].rsplit("/", 1)[1]
                label = binding.get("name", {}).get("value", "")
                labels[qid] = label
                start, end = day(binding.get("start")), day(binding.get("end"))
                records[(year, qid)].append(
                    {
                        "start": start,
                        "end": end,
                        "cause": binding.get("causeLabel", {}).get("value", ""),
                        "district": binding.get("districtLabel", {}).get("value", ""),
                    }
                )
                names[(year, fold(label))].add((qid, binding.get("districtLabel", {}).get("value", "")))
                if start is not None:
                    starts[year][start] += 1
                if end is not None:
                    ends[year][end] += 1
            term_start = {year: counter.most_common(1)[0][0] for year, counter in starts.items()}
            term_end = {year: counter.most_common(1)[0][0] for year, counter in ends.items()}
            members = conn.execute(
                """select e.date_from, ca.person_id, pn.text, an.text, o.party_id
                   from candidacy ca join contest_option o on o.id = ca.option_id join contest c on c.id = o.contest_id
                   join election e on e.id = c.election_id join name pn on pn.entity_id = ca.person_id and pn.is_preferred
                   join name an on an.entity_id = c.district_area_id and an.is_preferred
                   where c.body_id = %s and e.date_from < date '2026-01-01'
                     and (ca.role = 'list_member' or exists (
                       select 1 from outcome oc join result_set rs on rs.id = oc.result_set_id
                       where oc.option_id = o.id and oc.kind = 'elected' and rs.is_default))""",
                [body],
            ).fetchall()
            crowd = Counter((when.year, fold(name)) for when, _, name, _, _ in members)
            regions: dict[int, dict[tuple[str, ...], UUID]] = defaultdict(dict)
            constituencies: dict[int, set[tuple[str, ...]]] = defaultdict(set)
            for when, contest, kind, district in conn.execute(
                """select e.date_from, c.id, c.kind, an.text from contest c join election e on e.id = c.election_id
                   join name an on an.entity_id = c.district_area_id and an.is_preferred
                   where c.body_id = %s and e.date_from < date '2026-01-01'""",
                [body],
            ).fetchall():
                if kind == "list_allocation":
                    regions[when.year][seat(district)] = contest
                else:
                    constituencies[when.year].add(seat(district))
            seated: dict[int, set[str]] = defaultdict(set)
            for when, person, name, district, party in members:
                year = when.year
                key = (year, fold(name))
                qid = None
                options = names.get(key, set())
                if len({q for q, _ in options}) == 1 and crowd[key] == 1:
                    candidate, label = next(iter(options))
                    if not label or seat(label) == seat(district):
                        qid = candidate
                if qid is None and len(owned[person]) == 1:
                    candidate = next(iter(owned[person]))
                    if (year, candidate) in records:
                        qid = candidate
                member_record = None
                if qid is not None and owned[person] - {qid}:
                    qid = None
                if qid is not None:
                    found = records.get((year, qid), [])
                    member_record = found[0] if len(found) == 1 else None
                    seated[year].add(qid)
                    if not owned[person]:
                        owners = held.get(qid, set()) - {person}
                        if len(owners) == 1 and next(iter(owners)) not in cs.survivors and person not in cs.survivors:
                            cs.merge(next(iter(owners)), person)
                            totals["joined"] += 1
                        elif not owners:
                            cs.add("external_id", entity_id=person, scheme="wikidata", value=qid)
                            held[qid].add(person)
                            owned[person].add(qid)
                            totals["linked"] += 1
                else:
                    unmatched.append((name, f"{body_name} {year} {district}: no exact Wikidata match, default term dates"))
                if year not in term_start or year not in term_end:
                    raise ValueError(f"{body_name} {year}: no Wikidata term dates")
                start = member_record["start"] if member_record and member_record["start"] else term_start[year]
                end = member_record["end"] if member_record and member_record["end"] else term_end[year]
                early = end < term_end[year] - timedelta(days=7)
                tenure = cs.key(f"{SOURCE}.tenure", f"{body_name}:{year}:{person}")
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    body_id=body,
                    from_date=start,
                    from_precision="day",
                    to_date=end,
                    to_precision="day",
                    acquired_via="elected",
                    ended_via=(ended(member_record["cause"]) if early else "term_end") if member_record else "term_end",
                )
                totals["tenures"] += 1
                if party is not None:
                    cs.add("tenure_affiliation", tenure_id=tenure, kind="party", party_id=party, valid_from=start)
            for (year, qid), found in sorted(records.items()):
                if qid in seated[year] or len(found) != 1:
                    continue
                record = found[0]
                if record["start"] is None or record["start"] <= term_start[year] + timedelta(days=7):
                    unmatched.append((labels[qid], f"{body_name} {year}: on Wikidata for the whole term but not elected in our results"))
                    continue
                district = seat(record["district"]) if record["district"] else None
                contest = regions[year].get(district) if district else None
                if contest is None and (district is None or district not in constituencies[year]):
                    unmatched.append((labels[qid], f"{body_name} {year}: replacement from {record['start']} with district {record['district']!r} not placed"))
                    continue
                owners = held.get(qid, set())
                if len(owners) > 1:
                    unmatched.append((labels[qid], f"{body_name} {year}: Wikidata item held by several people"))
                    continue
                if owners:
                    person = next(iter(owners))
                else:
                    person = cs.key(f"{SOURCE}.person", qid)
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=labels[qid], lang="en", kind="official", is_preferred=True)
                    cs.add("external_id", entity_id=person, scheme="wikidata", value=qid)
                    held[qid].add(person)
                    totals["minted"] += 1
                end = record["end"] or term_end[year]
                early = end < term_end[year] - timedelta(days=7)
                cs.add(
                    "tenure",
                    id=cs.key(f"{SOURCE}.tenure", f"{body_name}:{year}:{person}"),
                    person_id=person,
                    body_id=body,
                    from_date=record["start"],
                    from_precision="day",
                    to_date=end,
                    to_precision="day",
                    acquired_via="list_replacement" if contest is not None else "elected",
                    contest_id=contest,
                    ended_via=ended(record["cause"]) if early else "term_end",
                )
                totals["replacements"] += 1
                samples.append((body_name, year, labels[qid], str(record["start"]), record["district"]))
        UNMATCHED.parent.mkdir(parents=True, exist_ok=True)
        UNMATCHED.write_text("\n".join(f"{name}\t{why}" for name, why in sorted(unmatched)) + "\n", encoding="utf-8")
        print(dict(totals), f"{len(unmatched)} listed in {UNMATCHED}")
        for sample in samples[:10]:
            print(sample)
        return cs.write()
