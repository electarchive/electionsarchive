import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.wikidata_links"
VERSION = "1"
TITLE = "Positions held (P39) of American politicians"
CHUNK = 150
SLACK = 1
POSITIONS = """SELECT ?item ?posLabel ?start ?end WHERE {{ VALUES ?item {{ {items} }}
?item p:P39 ?s . ?s ps:P39 ?pos .
OPTIONAL {{ ?s pq:P580 ?start }} OPTIONAL {{ ?s pq:P582 ?end }}
SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}"""
STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
LEVELS = {
    "us_house": re.compile(r"(house|representative)"),
    "us_senate": re.compile(r"senat"),
    "president": re.compile(r"president of the united states"),
    "state_upper": re.compile(r"senate"),
    "state_lower": re.compile(r"(house|assembly|delegates|general court)"),
    "governor": re.compile(r"governor"),
    "statewide_exec": re.compile(
        r"(attorney general|secretary of state|treasurer|auditor|comptroller|controller|superintendent"
        r"|commissioner|lieutenant|agriculture|insurance|labor)"
    ),
}
STATEMENTS = (
    """create temp table uspol as
with recursive t(id) as (
    select id from polity where iso_code = 'US'
    union select pp.polity_id from polity_parent pp join t on pp.parent_id = t.id
)
select distinct id from t""",
    """create temp table pstate as
with recursive up(id, anc) as (
    select id, id from uspol
    union select up.id, pp.parent_id from up join polity_parent pp on pp.polity_id = up.anc
)
select up.id, min(substr(p.iso_code, 4)) as st
from up join polity p on p.id = up.anc
where p.kind in ('federal_unit', 'region')
group by up.id""",
    """create temp table cc as
select c.id, extract(year from e.date_from)::int as year, coalesce(ps.st, 'US') as st,
    case
        when c.kind = 'ballot_question' then
            case when p.kind in ('sovereign_state', 'federal_unit', 'region') then 'statewide_measure'
            else 'local_measure' end
        when c.kind in ('elector_slate', 'delegate_allocation') or oe.value in ('president', 'vice_president')
            then 'president'
        when be.value = 'house' then 'us_house'
        when be.value = 'senate' then 'us_senate'
        when o.kind = 'governor' then 'governor'
        when b.kind in ('upper_house', 'unicameral') and op.kind in ('federal_unit', 'region') then 'state_upper'
        when b.kind = 'lower_house' and op.kind in ('federal_unit', 'region') then 'state_lower'
        when op.kind in ('federal_unit', 'region') and oe.value ~ '(justice|judge|court)' then 'judicial'
        when op.kind in ('federal_unit', 'region') then 'statewide_exec'
        when op.kind = 'school_district' or coalesce(oe.value, be.value) ~ '(school|education)' then 'school'
        when op.kind = 'county' then 'county'
        when op.kind = 'municipality' then 'municipal'
        else 'special_district'
    end as level,
    case
        when e.kind = 'runoff' then 'runoff'
        when c.party_scope_id is not null or e.kind in ('primary', 'caucus') then 'primary'
        when e.kind in ('special', 'by_election') or c.term = 'remainder' then 'special'
        else 'general'
    end as stage,
    c.default_result_kind = 'tacit' as tacit,
    cs.pipeline
from contest c
join election e on e.id = c.election_id
join uspol u on u.id = e.polity_id
join polity p on p.id = e.polity_id
left join pstate ps on ps.id = e.polity_id
left join office o on o.id = c.office_id
left join body b on b.id = c.body_id
left join polity op on op.id = coalesce(o.polity_id, b.polity_id, e.polity_id)
left join external_id oe on oe.entity_id = c.office_id and oe.scheme = 'us.office'
left join external_id be on be.entity_id = c.body_id and be.scheme = 'us.body'
join changeset cs on cs.id = c.changeset_id
where e.date_from is not null""",
    """create temp table pc as
select c.person_id, cc.st, cc.level, cc.year, cc.id contest_id
from candidacy c join contest_option co on co.id=c.option_id join cc on cc.id=co.contest_id
where cc.level in ('us_house','us_senate','governor','statewide_exec','state_upper','state_lower','president','judicial')""",
    """create index on pc(person_id)""",
    """create temp table pn as
select pc.person_id, pc.st, array_agg(distinct pc.level) levels, min(year) y0, max(year) y1, count(*) n,
 (select n.text from name n where n.entity_id=pc.person_id order by is_preferred desc, length(text) limit 1) nm
from pc group by 1,2""",
    r"""create temp table pk as
select *, regexp_replace(lower(split_part(regexp_replace(nm,'\s+(jr|sr|ii|iii|iv)\.?$','','i'),' ',1)),'[^a-z]','','g') f,
 regexp_replace(lower((regexp_split_to_array(regexp_replace(regexp_replace(nm,'\s+(jr|sr|ii|iii|iv)\.?,?$','','i'),',.*',''),'\s+'))[array_length(regexp_split_to_array(regexp_replace(regexp_replace(nm,'\s+(jr|sr|ii|iii|iv)\.?,?$','','i'),',.*',''),'\s+'),1)]),'[^a-z]','','g') l
from pn where nm !~ ','""",
    """create index on pk(st,l)""",
)
PAIRS = """
select a.person_id, b.person_id, a.st, a.levels, b.levels, a.y0, a.y1, b.y0, b.y1, wa.value, wb.value
from pk a join pk b on a.st = b.st and a.l = b.l and a.person_id < b.person_id and left(a.f, 3) = left(b.f, 3)
  and not (a.levels && b.levels)
left join external_id wa on wa.entity_id = a.person_id and wa.scheme = 'wikidata'
left join external_id wb on wb.entity_id = b.person_id and wb.scheme = 'wikidata'
where length(a.l) > 3 and (wa.value is null) <> (wb.value is null)
  and (a.nm = b.nm or lower(regexp_replace(a.nm, '[^a-zA-Z ]', '', 'g')) = lower(regexp_replace(b.nm, '[^a-zA-Z ]', '', 'g')))
  and not exists (select 1 from pc x join pc y on x.contest_id = y.contest_id
                  where x.person_id = a.person_id and y.person_id = b.person_id)
  and greatest(a.y0, b.y0) <= least(a.y1, b.y1) + 4
  and not exists (select 1 from person p join person q on q.birth_date <> p.birth_date
                  where p.id = a.person_id and q.id = b.person_id)
"""


def _pairs(conn) -> list[tuple]:
    for statement in STATEMENTS:
        conn.execute(statement)
    return conn.execute(PAIRS).fetchall()


def _urls(pairs: list[tuple]) -> list[str]:
    items = sorted({wa or wb for *_, wa, wb in pairs}, key=lambda item: int(item[1:]))
    return [
        "https://query.wikidata.org/sparql?"
        + urlencode(
            {
                "query": POSITIONS.format(items=" ".join(f"wd:{item}" for item in items[start : start + CHUNK])),
                "format": "json",
            }
        )
        for start in range(0, len(items), CHUNK)
    ]


def fetch() -> None:
    with connect() as conn:
        for url in _urls(_pairs(conn)):
            raw.fetch(SOURCE, url, suffix=".json")


def emit() -> Path:
    with connect() as conn:
        pairs = _pairs(conn)
        urls = _urls(pairs)
        held: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
        for url in urls:
            for binding in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]:
                held[binding["item"]["value"].rsplit("/", 1)[1]].append(
                    (
                        binding["posLabel"]["value"].lower(),
                        int(binding["start"]["value"][:4])
                        if binding.get("start", {}).get("value", "http")[:4].isdigit()
                        else -9999,
                        int(binding["end"]["value"][:4])
                        if binding.get("end", {}).get("value", "http")[:4].isdigit()
                        else 9999,
                    )
                )
        confirmed: list[tuple[UUID, UUID]] = []
        for a, b, state, a_levels, b_levels, a_from, a_to, b_from, b_to, a_item, b_item in pairs:
            known, other, levels, first, last = (
                (a, b, b_levels, b_from, b_to) if a_item else (b, a, a_levels, a_from, a_to)
            )
            name = STATES[state].lower() if state in STATES else None
            if name is not None and any(
                LEVELS[level].search(label)
                and (
                    ("united states" in label or "u.s." in label)
                    if level in ("us_house", "us_senate", "president")
                    else name in label
                    and not ("united states" in label or "u.s." in label)
                    and not (level in ("state_upper", "governor") and "lieutenant" in label)
                )
                and start <= last + SLACK
                and end >= first - SLACK
                for level in levels
                for label, start, end in held[a_item or b_item]
            ):
                confirmed.append((other, known))
        partners: dict[UUID, set[UUID]] = defaultdict(set)
        holders: dict[UUID, set[UUID]] = defaultdict(set)
        for other, known in confirmed:
            partners[known].add(other)
            holders[other].add(known)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "People recorded once per office joined to the Wikidata person who held that office in that state "
                "during the years they ran, where the names match and Wikidata's positions held cover both records"
            ),
            polity="US",
            kind="match",
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
        for url in urls:
            cs.artifact(raw.cached(SOURCE, url), source)
        merged = ambiguous = 0
        for other, known in confirmed:
            if len(holders[other]) > 1 or len(partners[known]) > 1:
                ambiguous += 1
                continue
            cs.merge(other, known)
            merged += 1
        print(
            f"{len(pairs)} candidate pairs, {len(confirmed)} confirmed by Wikidata, {merged} merged, {ambiguous} ambiguous left apart"
        )
        return cs.write()
