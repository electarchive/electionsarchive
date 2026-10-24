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
VERSION = "2"
TITLE = "Members of the Scottish Parliament and the Senedd (P39) on Wikidata"
BODIES = {
    "Scottish Parliament": ("Q1711695", r"the (\d\w\w) Scottish"),
    "Senedd Cymru": ("Q3406079", r"the (\d\w\w) (?:National Assembly for Wales|Senedd)"),
}
TERMS = {"1st": 1999, "2nd": 2003, "3rd": 2007, "4th": 2011, "5th": 2016, "6th": 2021, "7th": 2026}
WIKIPEDIA = "https://en.wikipedia.org/w/index.php?title={}&action=raw"
CHANGE_PAGES = {year: WIKIPEDIA.format(f"{ordinal}_Scottish_Parliament") for ordinal, year in TERMS.items()}
SENEDD_CHANGES = WIKIPEDIA.format("5th_National_Assembly_for_Wales")
MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        1,
    )
}
PARTIES = {
    "labour": "Labour Party",
    "scottish labour": "Labour Party",
    "scottish labour party": "Labour Party",
    "welsh labour": "Labour Party",
    "labour co-operative": "Labour Party",
    "labour and co-operative": "Labour Party",
    "scottish national party": "Scottish National Party",
    "conservative": "Conservative",
    "conservative party (uk)": "Conservative",
    "scottish conservatives": "Conservative",
    "scottish conservative party": "Conservative",
    "scottish conservative and unionist party": "Conservative",
    "welsh conservatives": "Conservative",
    "ukip": "UK Independence Party",
    "uk independence party": "UK Independence Party",
    "scottish liberal democrats": "Liberal Democrats",
    "liberal democrats": "Liberal Democrats",
    "scottish greens": "Scottish Green Party",
    "scottish green party": "Scottish Green Party",
    "scottish socialist party": "Scottish Socialist Party",
    "solidarity (scotland)": "Solidarity",
    "alba party": "Alba Party",
    "reform uk": "Reform UK",
    "reform uk scotland": "Reform UK",
    "brexit party": "Reform UK",
    "abolish the welsh assembly party": "Abolish the Welsh Assembly Party",
    "plaid cymru": "Plaid Cymru",
    "forward wales": "Forward Wales",
}
MERGED = {"Q14420732": "Rhun ap Iorwerth"}
RENAMED = {"Ash Denham": "Ash Regan"}
INDEPENDENT = {"independent", "independent politician", "independent (politician)"}
WALES_CHANGES = (
    (
        "2005-04-01",
        "month",
        "Peter Law",
        "Labour",
        "Independent politician",
        "Left Labour in protest at all-woman shortlists, and sat as an independent until his death in 2006.",
    ),
    (
        "2009-12-08",
        "day",
        "Mohammad Asghar",
        "Plaid Cymru",
        "Conservative",
        "Defected from Plaid Cymru to the Conservatives.",
    ),
    (
        "2010-04-16",
        "day",
        "Mick Bates",
        "Liberal Democrats",
        "Independent politician",
        "Sat as an independent after it emerged he would be prosecuted.",
    ),
    (
        "2022-11-08",
        "day",
        "Rhys ab Owen",
        "Plaid Cymru",
        "Independent politician",
        "Suspended pending a standards investigation.",
    ),
    (
        "2025-04-14",
        "day",
        "Russell George",
        "Conservative",
        "Independent politician",
        "Suspended after being charged with betting offences.",
    ),
    ("2025-07-22", "day", "Laura Anne Jones", "Conservative", "Reform UK", "Defected to Reform UK."),
    ("2026-01-20", "day", "James Evans", "Conservative", "Independent politician", "Expelled from the Conservatives."),
    ("2026-02-05", "day", "James Evans", "Independent politician", "Reform UK", "Joined Reform UK."),
)
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
    for url in (*CHANGE_PAGES.values(), SENEDD_CHANGES):
        raw.fetch(SOURCE, url, suffix=".txt")


def ended(cause: str) -> str | None:
    text = cause.lower()
    for needle, result in (
        ("resign", "resigned"),
        ("death", "died"),
        ("died", "died"),
        ("dies", "died"),
        ("disqualif", "disqualified"),
        ("expel", "expelled"),
        ("house of lords", "elevated"),
        ("won election to the uk parliament", "resigned"),
    ):
        if needle in text:
            return result
    return None


def names_in(text: str) -> list[str]:
    return [(match[1] or match[0]).strip() for match in re.findall(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", text)]


def parse_changes() -> dict[str, list[dict[str, object]]]:
    events: dict[str, list[dict[str, object]]] = {"Scottish Parliament": [], "Senedd Cymru": []}
    for year, url in CHANGE_PAGES.items():
        text = re.sub(
            r"<ref[^>]*/>|<ref.*?</ref>", "", raw.cached(SOURCE, url).path.read_text(encoding="utf-8"), flags=re.DOTALL
        )
        for template in re.findall(r"\{\{Scottish Parliament Changes\s*\n(.*?)\n\}\}", text, re.DOTALL):
            fields = dict(
                re.findall(
                    r"\|\s*(month|date|year|con|gain|loss|note)\s*=\s*(.*?)(?=\s*\|\s*(?:month|date|year|con|gain|loss|note)\s*=|\Z)",
                    template,
                    re.DOTALL,
                )
            )
            when = date(
                int(re.search(r"\d{4}", fields["year"])[0]),
                MONTHS[fields["month"].strip().lower()],
                int(re.search(r"\d+", fields["date"])[0]),
            )
            note = fields.get("note", "").strip()
            events["Scottish Parliament"].append(
                {
                    "date": when,
                    "precision": "day",
                    "names": names_in(note),
                    "old": names_in(fields.get("loss", ""))[:1]
                    or [re.sub(r"[\[\]]", "", fields.get("loss", "")).strip()],
                    "new": names_in(fields.get("gain", ""))[:1]
                    or [re.sub(r"[\[\]]", "", fields.get("gain", "")).strip()],
                    "note": re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", note),
                    "term": year,
                }
            )
    table = re.sub(
        r"<ref[^>]*/>|<ref.*?</ref>",
        "",
        raw.cached(SOURCE, SENEDD_CHANGES).path.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )
    table = table[table.index("Reason for") :]
    table = table[: table.index("\n|}")]
    last: str | None = None
    for row in table.split("\n|-")[1:]:
        found = re.search(r"(\d{1,2}) (\w+) (\d{4})", row)
        parties = re.findall(r"\{\{Party name with colour\|([^}|]*)", row)
        template_ends = [match.end() for match in re.finditer(r"\{\{Party name with colour\|[^}]*\}\}", row)]
        if found is None or len(parties) < 2:
            continue
        who = names_in(row)
        names = [*who[:2], last] if re.search(r"re-?admitted", row, re.IGNORECASE) and last is not None else who[:2]
        last = who[0]
        events["Senedd Cymru"].append(
            {
                "date": date(int(found[3]), MONTHS[found[2].lower()], int(found[1])),
                "precision": "day",
                "names": names,
                "old": [parties[0]],
                "new": [parties[1]],
                "note": re.sub(
                    r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]",
                    r"\1",
                    re.sub(r"\{\{.*?\}\}", "", row[template_ends[1] :], flags=re.DOTALL),
                ).strip(" |\n"),
                "term": 2016,
            }
        )
    for when, precision, name, old, new, note in WALES_CHANGES:
        events["Senedd Cymru"].append(
            {
                "date": date.fromisoformat(when),
                "precision": precision,
                "names": [name],
                "old": [old],
                "new": [new],
                "note": note,
                "term": None,
            }
        )
    return events


VACANCY = re.compile(
    r"resign\w*\s+(?:from\s+(?:the\s+)?(?:Scottish\s+)?(?:Parliament|Assembly)|(?:his|her)\s+(?:regional\s+)?(?:seat|position)|as\s+an?\s+(?:MSP|AM)"
    r"|following|due\s+to|to\s+take\s+up)|\bdied\b|\bdies\b|\bdeath\b|found\s+dead|appointed\s+to\s+the\s+House\s+of\s+Lords",
    re.IGNORECASE,
)
BYELECTION = re.compile(r"\b(?:won|wins|holds)\b.*by-election", re.IGNORECASE)


def emit() -> Path:
    events = parse_changes()
    live = "not exists (select from entity_merge m where m.entity_id = x.entity_id)"
    with connect() as conn:
        held: dict[str, set[UUID]] = defaultdict(set)
        owned: dict[UUID, set[str]] = defaultdict(set)
        for person, value in conn.execute(
            f"select x.entity_id, x.value from external_id x join person p on p.id = x.entity_id where x.scheme = 'wikidata' and {live}"
        ).fetchall():
            held[value].add(person)
            owned[person].add(value)
        known_parties: dict[str, set[UUID]] = defaultdict(set)
        for party, text in conn.execute(
            """select distinct o.party_id, n.text from contest_option o join contest c on c.id = o.contest_id
               join body b on b.id = c.body_id join name bn on bn.entity_id = b.id and bn.is_preferred
               join name n on n.entity_id = o.party_id
               where bn.text in ('Scottish Parliament', 'Senedd Cymru')"""
        ).fetchall():
            known_parties[fold(text)].add(party)

        def party_of(label: str) -> tuple[bool, UUID | None]:
            key = fold(label)
            if key in INDEPENDENT or not key:
                return True, None
            found = known_parties.get(fold(PARTIES.get(key, label)), set())
            return (len(found) == 1, next(iter(found)) if len(found) == 1 else None)

        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Members of the Scottish Parliament and the Senedd with tenures, seats, party changes, and the members they replaced",
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
        wikipedia = cs.source("Wikipedia", "Changes in membership of the Scottish Parliament and the Welsh Assembly")
        cs.add(
            "source",
            id=wikipedia,
            kind="community",
            publisher="Wikipedia",
            title="Changes in membership of the Scottish Parliament and the Welsh Assembly",
            url="https://en.wikipedia.org/wiki/List_of_British_politicians_who_have_changed_party_affiliation",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Party changes, resignations, and replacements from the changes tables of Wikipedia's Scottish Parliament and National Assembly for Wales term articles, CC BY-SA 4.0.",
        )
        for url in (*CHANGE_PAGES.values(), SENEDD_CHANGES):
            cs.artifact(raw.cached(SOURCE, url), wikipedia)
        for qid, label in MERGED.items():
            holders = held.get(qid, set())
            targets = {
                person
                for (person,) in conn.execute(
                    "select p.id from person p join name n on n.entity_id = p.id and n.is_preferred where n.text = %s and not exists (select from entity_merge m where m.entity_id = p.id)",
                    [label],
                ).fetchall()
            } - holders
            if len(holders) == 1 and len(targets) == 1:
                cs.merge(next(iter(holders)), next(iter(targets)))
        unmatched: list[tuple[str, str]] = []
        totals = Counter()
        specs: list[dict[str, object]] = []
        for body_name, (item, pattern) in BODIES.items():
            body = conn.execute(
                "select b.id from body b join name n on n.entity_id = b.id and n.is_preferred where n.text = %s",
                [body_name],
            ).fetchone()[0]
            limit = date(2027, 1, 1) if body_name == "Scottish Parliament" else date(2026, 1, 1)
            records: dict[tuple[int, str], list[dict[str, object]]] = defaultdict(list)
            labels: dict[str, str] = {}
            names: dict[tuple[int, str], set[tuple[str, str]]] = defaultdict(set)
            starts: dict[int, Counter] = defaultdict(Counter)
            ends: dict[int, Counter] = defaultdict(Counter)
            for binding in json.loads(raw.cached(SOURCE, service(item)).path.read_text(encoding="utf-8"))["results"][
                "bindings"
            ]:
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
            term_end = {year: counter.most_common(1)[0][0] for year, counter in ends.items() if year < 2026}
            members = conn.execute(
                """select e.date_from, ca.person_id, pn.text, an.text, o.party_id, c.district_area_id, c.kind
                   from candidacy ca join contest_option o on o.id = ca.option_id join contest c on c.id = o.contest_id
                   join election e on e.id = c.election_id join name pn on pn.entity_id = ca.person_id and pn.is_preferred
                   join name an on an.entity_id = c.district_area_id and an.is_preferred
                   where c.body_id = %s and e.date_from < %s
                     and (ca.role = 'list_member' or exists (
                       select 1 from outcome oc join result_set rs on rs.id = oc.result_set_id
                       where oc.option_id = o.id and oc.kind = 'elected' and rs.is_default))""",
                [body, limit],
            ).fetchall()
            crowd = Counter((when.year, fold(name)) for when, _, name, _, _, _, _ in members)
            regions: dict[int, dict[tuple[str, ...], tuple[UUID, UUID]]] = defaultdict(dict)
            constituencies: dict[int, dict[tuple[str, ...], tuple[UUID, str]]] = defaultdict(dict)
            for when, contest, kind, district, area in conn.execute(
                """select e.date_from, c.id, c.kind, an.text, c.district_area_id from contest c join election e on e.id = c.election_id
                   join name an on an.entity_id = c.district_area_id and an.is_preferred
                   where c.body_id = %s and e.date_from < %s""",
                [body, limit],
            ).fetchall():
                if kind == "list_allocation":
                    regions[when.year][seat(district)] = (contest, area)
                else:
                    constituencies[when.year][seat(district)] = (area, district)
            seated: dict[int, set[str]] = defaultdict(set)
            origins: dict[tuple[int, str], dict[str, object]] = {}
            for when, person, name, district, party, area, kind in members:
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
                    ordered = sorted(found, key=lambda row: row["start"] or date.min)
                    home = [row for row in ordered if seat(row["district"]) == seat(ordered[0]["district"])]
                    member_record = (
                        {
                            "start": home[0]["start"],
                            "end": None if any(row["end"] is None for row in home) else max(row["end"] for row in home),
                            "cause": max(home, key=lambda row: row["end"] or date.max)["cause"],
                        }
                        if home
                        else None
                    )
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
                    unmatched.append(
                        (name, f"{body_name} {year} {district}: no exact Wikidata match, default term dates")
                    )
                if year not in term_start:
                    raise ValueError(f"{body_name} {year}: no Wikidata term dates")
                start = member_record["start"] if member_record and member_record["start"] else term_start[year]
                end = member_record["end"] if member_record and member_record["end"] else term_end.get(year)
                early = end is not None and (year not in term_end or end < term_end[year] - timedelta(days=7))
                spec = {
                    "body_name": body_name,
                    "body": body,
                    "year": year,
                    "person": cs.survivors.get(person, person),
                    "key": f"{body_name}:{year}:{person}",
                    "start": start,
                    "end": end,
                    "acquired": "elected",
                    "ended": None
                    if end is None
                    else (ended(member_record["cause"]) if early else "term_end")
                    if member_record
                    else "term_end",
                    "contest": None,
                    "party": party,
                    "area": area,
                    "region": kind == "list_allocation",
                    "district": district,
                    "label": name,
                    "alias": labels.get(qid, "") if qid is not None else "",
                    "origin": True,
                }
                specs.append(spec)
                if qid is not None:
                    origins[(year, qid)] = spec
            for (year, qid), found in sorted(records.items()):
                if year not in term_start or (year >= 2026 and body_name != "Scottish Parliament"):
                    continue
                if qid in seated[year]:
                    sat = origins.get((year, qid))
                    todo = [
                        row
                        for row in sorted(found, key=lambda row: row["start"] or date.min)[1:]
                        if sat is not None and row["district"] and seat(row["district"]) != seat(str(sat["district"]))
                    ]
                elif len(found) == 1:
                    todo = found
                else:
                    continue
                for record in todo:
                    if qid not in seated[year] and (
                        record["start"] is None or record["start"] <= term_start[year] + timedelta(days=7)
                    ):
                        unmatched.append(
                            (
                                labels[qid],
                                f"{body_name} {year}: on Wikidata for the whole term but not elected in our results",
                            )
                        )
                        continue
                    district = seat(record["district"]) if record["district"] else None
                    region = regions[year].get(district) if district else None
                    place = constituencies[year].get(district) if district else None
                    if region is None and place is None:
                        unmatched.append(
                            (
                                labels[qid],
                                f"{body_name} {year}: replacement from {record['start']} with district {record['district']!r} not placed",
                            )
                        )
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
                        cs.add(
                            "name", entity_id=person, text=labels[qid], lang="en", kind="official", is_preferred=True
                        )
                        cs.add("external_id", entity_id=person, scheme="wikidata", value=qid)
                        held[qid].add(person)
                        totals["minted"] += 1
                    end = record["end"] or term_end.get(year)
                    early = end is not None and (year not in term_end or end < term_end[year] - timedelta(days=7))
                    specs.append(
                        {
                            "body_name": body_name,
                            "body": body,
                            "year": year,
                            "person": person,
                            "key": f"{body_name}:{year}:{person}"
                            + (f":{record['start']}" if qid in seated[year] else ""),
                            "start": record["start"],
                            "end": end,
                            "acquired": "list_replacement" if region is not None else "elected",
                            "ended": (ended(record["cause"]) if early else "term_end") if end is not None else None,
                            "contest": region[0] if region is not None else None,
                            "party": origins[(year, qid)]["party"] if qid in seated[year] else None,
                            "area": region[1] if region is not None else place[0],
                            "region": region is not None,
                            "district": record["district"],
                            "label": labels[qid],
                            "origin": False,
                        }
                    )
                    totals["replacements"] += 1
            last_term = max(term_start)
            for spec in specs:
                if spec["body_name"] == body_name:
                    spec["term_end"] = term_end.get(spec["year"])
                    spec["last_term"] = last_term
        spec_names: dict[UUID, set[str]] = defaultdict(set)
        for person, text in conn.execute(
            "select entity_id, text from name where entity_id = any(%s)", [list({spec["person"] for spec in specs})]
        ).fetchall():
            spec_names[person].add(fold(text))
        for spec in specs:
            spec_names[spec["person"]].add(fold(str(spec["label"])))
            spec_names[spec["person"]].add(fold(str(spec.get("alias", ""))))
            spec_names[spec["person"]].add(fold(RENAMED.get(str(spec["label"]), "")))

        def find(body_name: str, name: str, when: date, slack: int = 3) -> dict[str, object] | None:
            matches = [
                spec
                for spec in specs
                if spec["body_name"] == body_name
                and fold(name) in spec_names[spec["person"]]
                and spec["start"] - timedelta(days=slack) <= when
                and (spec["end"] is None or when <= spec["end"] + timedelta(days=slack))
            ]
            return matches[0] if len(matches) == 1 else None

        def arrives(body_name: str, name: str, when: date) -> dict[str, object] | None:
            matches = [
                spec
                for spec in specs
                if spec["body_name"] == body_name
                and fold(name) in spec_names[spec["person"]]
                and when - timedelta(days=5) <= spec["start"] <= when + timedelta(days=60)
            ]
            return matches[0] if len(matches) == 1 else None

        for person, name_id, old in conn.execute(
            "select entity_id, id, text from name where is_preferred and text = any(%s) and entity_id = any(%s)",
            [list(RENAMED), list({spec["person"] for spec in specs})],
        ).fetchall():
            current = RENAMED[old]
            if (
                conn.execute("select 1 from name where entity_id = %s and text = %s", [person, current]).fetchone()
                is None
            ):
                cs.add("name", entity_id=person, text=current, lang="en", kind="official", is_preferred=False)
            else:
                cs.add(
                    "name_drop", name_id=name_id, entity_id=person, text=old, reason=f"known as {current} since 2022"
                )
                cs.add("name", entity_id=person, text=old, lang="en", kind="historical", is_preferred=False)
        switches: dict[int, list[dict[str, object]]] = defaultdict(list)
        replaced: dict[int, dict[str, object]] = {}
        for body_name, rows in events.items():
            for event in sorted(rows, key=lambda row: row["date"]):
                if any("presiding officer" in fold(str(value)) for value in event["new"]):
                    continue
                when, note = event["date"], str(event["note"])
                old_known, old = party_of(str(event["old"][0]))
                new_known, new = party_of(str(event["new"][0]))
                person = next(
                    (hit for name in event["names"] if (hit := find(body_name, name, when)) is not None), None
                )
                where = f"{body_name} {when} {event['names'][0]}"
                if VACANCY.search(note):
                    if person is None:
                        unmatched.append((str(event["names"][0]), f"{where}: leaver not found"))
                        continue
                    reason = ended(note)
                    if person["end"] is None or person["end"] > when + timedelta(days=7):
                        person["end"], person["ended"] = when, reason
                    elif person["ended"] in (None, "term_end"):
                        person["ended"] = reason
                    if reason in ("resigned", "died", "expelled"):
                        cs.add(
                            "tenure_event",
                            tenure_id=cs.key(f"{SOURCE}.tenure", str(person["key"])),
                            kind=reason,
                            date=when,
                            date_precision="day",
                            reason=note[:240],
                            source_id=wikipedia,
                        )
                    for name in event["names"]:
                        successor = arrives(body_name, name, when)
                        if successor is not None and successor is not person:
                            if successor["acquired"] == "list_replacement":
                                replaced[id(successor)] = person
                            if new_known and new is not None:
                                successor["party"] = new
                    continue
                if BYELECTION.search(note):
                    winner = arrives(body_name, str(event["names"][0]), when)
                    if winner is not None and new_known and new is not None and winner["party"] is None:
                        winner["party"] = new
                    elif winner is None:
                        unmatched.append((str(event["names"][0]), f"{where}: by-election winner not found"))
                    continue
                if person is None or not (old_known and new_known) or old == new:
                    if old != new or person is None:
                        unmatched.append(
                            (str(event["names"][0]), f"{where}: {event['old'][0]} to {event['new'][0]} not applied")
                        )
                    continue
                switches[id(person)].append(
                    {
                        "date": when,
                        "precision": event["precision"],
                        "party": new,
                        "kind": "expelled"
                        if re.search(r"expel", note, re.IGNORECASE)
                        else "became_independent"
                        if new is None
                        else "switched_party",
                        "note": note,
                    }
                )
                totals["switches"] += 1
        for spec in specs:
            if spec["acquired"] != "list_replacement" or id(spec) in replaced:
                continue
            candidates = [
                other
                for other in specs
                if other["body"] == spec["body"]
                and other["year"] == spec["year"]
                and other["region"]
                and other["area"] == spec["area"]
                and other["party"] is not None
                and other["end"] is not None
                and spec["start"] - timedelta(days=60) <= other["end"] <= spec["start"] + timedelta(days=3)
                and other is not spec
                and (spec["party"] is None or other["party"] == spec["party"])
            ]
            if len(candidates) == 1:
                replaced[id(spec)] = candidates[0]
            else:
                unmatched.append(
                    (
                        str(spec["label"]),
                        f"{spec['body_name']} {spec['year']}: list replacement from {spec['start']} has {len(candidates)} possible predecessors",
                    )
                )
        for spec in specs:
            predecessor = replaced.get(id(spec))
            if predecessor is not None and spec["party"] is None:
                spec["party"] = predecessor["party"]
        lists: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
        for spec in specs:
            if spec["region"] and spec["origin"]:
                lists[(spec["body"], spec["year"], spec["area"])].append(spec)
        slots: dict[int, int] = {}
        for members_of in lists.values():
            for number, spec in enumerate(
                sorted(members_of, key=lambda spec: (str(spec["party"]), str(spec["label"]))), 1
            ):
                slots[id(spec)] = number
        for spec in specs:
            origin = spec
            while id(origin) in replaced:
                origin = replaced[id(origin)]
            slots[id(spec)] = slots.get(id(origin), 0)
        seat_spans: dict[tuple[object, ...], dict[str, object]] = {}
        for spec in specs:
            number = slots[id(spec)] if spec["region"] else 0
            place = (spec["body"], spec["area"], number)
            span = seat_spans.setdefault(
                place,
                {
                    "body": spec["body"],
                    "area": spec["area"],
                    "label": f"{spec['district']} list seat {number}"
                    if spec["region"] and number
                    else str(spec["district"]),
                    "first": spec["start"],
                    "years": set(),
                },
            )
            span["first"] = min(span["first"], spec["start"])
            span["years"].add(spec["year"])
            spec["seat_key"] = place
        for place, span in seat_spans.items():
            years = span["years"]
            sample = next(spec for spec in specs if spec["seat_key"] == place)
            closes = max(years) < sample["last_term"]
            term_of = {spec["year"]: spec["term_end"] for spec in specs if spec["seat_key"] == place}
            cs.add(
                "seat",
                id=cs.key(f"{SOURCE}.seat", ":".join(str(part) for part in place)),
                body_id=span["body"],
                district_area_id=span["area"],
                label=span["label"],
                valid_from=span["first"],
                valid_from_precision="day",
                valid_to=term_of[max(years)] if closes else None,
                valid_to_precision="day",
            )
            totals["seats"] += 1
        for spec in specs:
            tenure = cs.key(f"{SOURCE}.tenure", str(spec["key"]))
            predecessor = replaced.get(id(spec))
            cs.add(
                "tenure",
                id=tenure,
                person_id=spec["person"],
                body_id=spec["body"],
                seat_id=cs.key(f"{SOURCE}.seat", ":".join(str(part) for part in spec["seat_key"])),
                from_date=spec["start"],
                from_precision="day",
                to_date=spec["end"],
                to_precision="day",
                acquired_via=spec["acquired"],
                contest_id=spec["contest"],
                ended_via=spec["ended"],
                succeeds_tenure_id=cs.key(f"{SOURCE}.tenure", str(predecessor["key"]))
                if predecessor is not None
                else None,
            )
            totals["tenures"] += 1
            spells = [(spec["party"], spec["start"], "day")] + [
                (row["party"], row["date"], row["precision"])
                for row in sorted(switches.get(id(spec), []), key=lambda row: row["date"])
            ]
            for index, (party, since, precision) in enumerate(spells):
                if party is None and index == 0:
                    continue
                until = spells[index + 1][1] if index + 1 < len(spells) else None
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=party,
                    valid_from=since,
                    valid_from_precision=precision,
                    valid_to=until,
                    valid_to_precision=spells[index + 1][2] if until is not None else "day",
                )
            for row in switches.get(id(spec), []):
                cs.add(
                    "tenure_event",
                    tenure_id=tenure,
                    kind=row["kind"],
                    date=row["date"],
                    date_precision=row["precision"],
                    reason=str(row["note"])[:240],
                    source_id=wikipedia,
                )
        UNMATCHED.parent.mkdir(parents=True, exist_ok=True)
        UNMATCHED.write_text("\n".join(f"{name}\t{why}" for name, why in sorted(unmatched)) + "\n", encoding="utf-8")
        print(dict(totals), f"{len(unmatched)} listed in {UNMATCHED}")
        return cs.write()
