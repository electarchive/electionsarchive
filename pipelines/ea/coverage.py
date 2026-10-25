import csv
import json
import time
from collections import defaultdict
from datetime import date

from ea import raw
from ea.sources.us.clerk import LEGISLATORS
from ea.store import ROOT, Connection, connect

LEVELS = (
    "president",
    "us_senate",
    "us_house",
    "governor",
    "statewide_exec",
    "judicial",
    "state_upper",
    "state_lower",
    "county",
    "municipal",
    "school",
    "special_district",
    "statewide_measure",
    "local_measure",
)
STAGES = ("general", "primary", "runoff", "special")
LOCAL = {"county", "municipal", "school", "special_district", "local_measure"}
ERAS = ((1789, 1823), (1824, 1899), (1900, 1966), (1967, 1999), (2000, 2015), (2016, 2025))
LAST = 2025
ALL_LEVELS = ("us_house", "us_senate", "governor", "statewide_exec", "state_upper", "state_lower")
CONGRESS = ("us_house", "us_senate")
STATEWIDE = ("governor", "statewide_exec", "state_upper", "state_lower")
RUNOFFS = (
    ("AL AR GA MS OK SC TX", "primary", ALL_LEVELS, 1900, LAST, 0.5),
    ("FL", "primary", ALL_LEVELS, 1900, 2000, 0.5),
    ("SD", "primary", ("us_house", "us_senate", "governor"), 1900, LAST, 0.35),
    ("GA", "general", ALL_LEVELS, 1964, LAST, 0.5),
    ("LA", "general", STATEWIDE, 1975, LAST, 0.5),
    ("LA", "general", CONGRESS, 1978, 2006, 0.5),
    ("LA", "general", CONGRESS, 2012, LAST, 0.5),
    ("GA LA MS TX", "special", ALL_LEVELS, 1980, LAST, 0.5),
)

CONTESTS_SQL = """
create temp table uspol as
with recursive t(id) as (
    select id from polity where iso_code = 'US'
    union select pp.polity_id from polity_parent pp join t on pp.parent_id = t.id
)
select distinct id from t;

create temp table pstate as
with recursive up(id, anc) as (
    select id, id from uspol
    union select up.id, pp.parent_id from up join polity_parent pp on pp.polity_id = up.anc
)
select up.id, min(substr(p.iso_code, 4)) as st
from up join polity p on p.id = up.anc
where p.kind in ('federal_unit', 'region')
group by up.id;

create temp table cc as
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
where e.date_from is not null and not c.notional;

analyze cc;

update cc set year = coalesce(
        (select 1786 + 2 * max(n) from (select min(t.from_date) as d from tenure t
             join contest c on c.id = t.contest_id join election e on e.id = c.election_id
             where t.contest_id = cc.id and t.from_date > e.date_from) f
         cross join generate_series(1, 119) n
         where f.d >= case when n <= 73 then make_date(1787 + 2 * n, 3, 4)
             else make_date(1787 + 2 * n, 1, 3) end),
        (select case when year % 2 = 1 then year - 1
             when e.date_from < make_date(year, case when year < 1935 then 3 else 1 end, case when year < 1935 then 4 else 3 end)
             then year - 2 else year end
         from contest c join election e on e.id = c.election_id where c.id = cc.id))
where level = 'us_house' and stage = 'general';

create index on cc (id);

delete from district_county x
where not exists (select from area_geometry g where g.area_id = x.district_area_id and g.valid = x.district_valid)
    or not exists (select from area_geometry g where g.area_id = x.county_area_id and g.valid = x.county_valid);

create temp table cty as
select g.area_id, g.valid, g.geom from area_geometry g join area a on a.id = g.area_id and a.scheme_code = 'us.county';

create index on cty using gist (geom);

analyze cty;

create temp table dp as
select dg.area_id, dg.valid, st_pointonsurface(dg.geom) as pt, d.dates
from (select c.district_area_id as area_id, g.valid, array_agg(distinct e.date_from) as dates
      from cc join contest c on c.id = cc.id join election e on e.id = c.election_id
      join area_geometry g on g.area_id = c.district_area_id and g.valid @> e.date_from
      where cc.level in ('us_house', 'state_upper', 'state_lower')
      group by 1, 2) d
join area_geometry dg on dg.area_id = d.area_id and dg.valid = d.valid
where st_area(dg.geom) > 0;

create temp table dn as
select distinct dp.area_id, dp.valid, c.area_id as county_area_id, c.valid as county_valid
from dp cross join unnest(dp.dates) t(d)
join cty c on c.valid @> t.d and st_intersects(c.geom, dp.pt)
where not exists (select from district_county x where x.district_area_id = dp.area_id and x.district_valid = dp.valid
    and x.county_area_id = c.area_id and x.county_valid = c.valid);

insert into district_county (district_area_id, district_valid, county_area_id, county_valid, share)
select dn.area_id, dn.valid, dn.county_area_id, dn.county_valid, st_area(st_intersection(c.geom, dg.geom)) / st_area(dg.geom)
from dn
join area_geometry dg on dg.area_id = dn.area_id and dg.valid = dn.valid
join cty c on c.area_id = dn.county_area_id and c.valid = dn.county_valid
on conflict do nothing;

update district_county x set rest = coalesce((
    select max(st_area(st_intersection(c.geom, dg.geom)) / st_area(dg.geom))
    from area_geometry dg
    join cty c on c.area_id <> x.county_area_id and c.geom && dg.geom
        and c.valid @> coalesce(lower(x.district_valid * x.county_valid), upper(x.district_valid * x.county_valid) - 1,
            current_date)
    where dg.area_id = x.district_area_id and dg.valid = x.district_valid), 0)
where x.rest is null and x.share >= 0.5 and x.share < 0.98;

create temp table sc as
select distinct cc.id
from cc join contest c on c.id = cc.id join election e on e.id = c.election_id
join district_county x on x.district_area_id = c.district_area_id and x.district_valid @> e.date_from
    and x.county_valid @> e.date_from
where cc.level in ('us_house', 'state_upper', 'state_lower') and (x.share >= 0.98 or x.rest <= 0.02);

create temp table rs as
select r.k, r.contest_id, cs.pipeline,
    s.publisher in ('Inter-university Consortium for Political and Social Research', 'Algara and Amlani') as derived,
    coalesce(r.completeness, 1) >= 0.98 or exists (
        select from result_set d where d.contest_id = r.contest_id and d.is_default and d.status = 'active' and d.k <> r.k
            and exists (select from contest_option o join result_value v on v.option_k = o.k and v.result_set_k = d.k
                where o.contest_id = r.contest_id and o.kind = 'candidate' and v.measure = 1 and v.value > 0)
            and not exists (select from contest_option o where o.contest_id = r.contest_id and o.kind = 'candidate'
                and coalesce((select sum(v.value) from result_value v
                    where v.result_set_k = r.k and v.option_k = o.k and v.measure = 1), 0)
                <> coalesce((select sum(v.value) from result_value v
                    where v.result_set_k = d.k and v.option_k = o.k and v.measure = 1), 0))) as whole
from result_set r join cc on cc.id = r.contest_id join changeset cs on cs.id = r.changeset_id
left join source s on s.id = r.source_id
where r.status <> 'withdrawn';

create table if not exists coverage_cache_unit (result_set_k integer not null, unit_k integer not null,
    primary key (result_set_k, unit_k));

create table if not exists coverage_cache_measure (result_set_k integer not null, measure smallint not null,
    primary key (result_set_k, measure));

create table if not exists coverage_cache_unit_k (unit_k integer primary key);

create table if not exists coverage_cache_done (result_set_k integer primary key);

create temp table fresh as
select r.k from result_set r where not exists (select from coverage_cache_done d where d.result_set_k = r.k);

insert into coverage_cache_unit
select distinct rv.result_set_k, rv.unit_k from result_value rv where rv.result_set_k in (select k from fresh)
on conflict do nothing;

insert into coverage_cache_measure
select distinct rv.result_set_k, rv.measure from result_value rv
where rv.measure in (2, 3, 14, 24, 25) and rv.result_set_k in (select k from fresh)
on conflict do nothing;

insert into coverage_cache_unit_k
select distinct unit_k from coverage_cache_unit
where not exists (select from coverage_cache_unit_k)
on conflict do nothing;

insert into coverage_cache_unit_k
select distinct unit_k from coverage_cache_unit where result_set_k in (select k from fresh)
on conflict do nothing;

insert into coverage_cache_done select k from fresh on conflict do nothing;

analyze coverage_cache_unit;

analyze coverage_cache_measure;

analyze coverage_cache_unit_k;

create temp view rsm as
select cm.result_set_k, cm.measure from coverage_cache_measure cm
join result_set r on r.k = cm.result_set_k and r.status <> 'withdrawn';

create temp table tn as
select cc.id from cc join contest c on c.id = cc.id
where exists (select from result_set r join rsm on rsm.result_set_k = r.k and rsm.measure in (2, 24)
        where r.election_id = c.election_id and (r.contest_id is null or r.contest_id = c.id))
    and exists (select from result_set r join rsm on rsm.result_set_k = r.k and rsm.measure in (3, 14, 25)
        where r.election_id = c.election_id and (r.contest_id is null or r.contest_id = c.id));

create temp view rsu as select result_set_k, unit_k from coverage_cache_unit;

create temp table ug as
select u.k,
    u.scheme_code,
    case when u.scheme_code in ('us.precinct', 'us.nyc_ed', 'us-ia.caucus_precinct') then 'precinct'
        when u.scheme_code in ('us.county', 'us.town', 'us.county_part', 'us.former_county', 'us.ak_house_district',
            'us.place') then 'county'
        else 'total' end as grain,
    u.area_relation = 'equals' and g.area_id is not null as shaped
from reporting_unit u
left join (select distinct area_id from area_geometry) g on g.area_id = u.area_id
where u.k in (select unit_k from coverage_cache_unit_k);

create temp table cd as
select cc.id as contest_id, cc.st, e.date_from as d
from cc join contest c on c.id = cc.id join election e on e.id = c.election_id
where cc.year >= 2000 and cc.level in ('president', 'us_senate', 'governor', 'statewide_exec', 'judicial', 'statewide_measure')
    and not (cc.level = 'judicial' and exists (select from external_id y where y.entity_id = cc.id
        and y.scheme = 'us.contest' and y.value ~ '(appell|appeal|circuit|district|supreme-court:[0-9])'));

create temp table cx as
select s.st, s.d, count(u.k) as n
from (select distinct st, d from cd) s
left join (reporting_unit u join pstate ps on ps.id = u.polity_id) on u.scheme_code = 'us.county' and u.code <> '15005' and ps.st = s.st and u.valid @> s.d
group by 1, 2;

create temp table cu as
select cd.contest_id, cx.n as expected, count(distinct u.k) as present
from cd join cx on cx.st = cd.st and cx.d = cd.d
left join (rs join rsu on rsu.result_set_k = rs.k join reporting_unit u on u.k = rsu.unit_k and u.scheme_code = 'us.county')
    on rs.contest_id = cd.contest_id and u.valid @> cd.d
group by cd.contest_id, cx.n;

create temp table cg as
select rs.contest_id,
    bool_or(ug.grain = 'precinct' and rs.whole) as precinct,
    bool_or(ug.grain in ('county', 'precinct')) as any_county,
    bool_or(ug.grain = 'county' and rs.whole and (ug.scheme_code <> 'us.county' or cu.contest_id is null
        or cu.expected = 0 or cu.present >= 0.9 * cu.expected)) as county,
    bool_or(ug.grain = 'county' and rs.whole and (ug.scheme_code <> 'us.county' or cu.contest_id is null
        or cu.expected = 0 or cu.present >= 0.9 * cu.expected) and not coalesce(rs.derived, false)) as county_free
from rs join rsu on rsu.result_set_k = rs.k join ug on ug.k = rsu.unit_k
left join cu on cu.contest_id = rs.contest_id
group by rs.contest_id;
"""

CACHE_SQL = CONTESTS_SQL[
    CONTESTS_SQL.index("create table if not exists coverage_cache_unit ") : CONTESTS_SQL.index("create temp view rsm")
]

COUNT_SQL = """
with c as (
    select cc.*, cg.precinct, cg.county, cg.county_free, cg.any_county, exists (select from rs where rs.contest_id = cc.id) as results,
        (exists (select from sc where sc.id = cc.id) or cc.st = 'AK' and cc.level = 'state_lower')
            and exists (select from rs where rs.contest_id = cc.id and rs.whole) as single,
        (exists (select from sc where sc.id = cc.id) or cc.st = 'AK' and cc.level = 'state_lower')
            and exists (select from rs where rs.contest_id = cc.id and rs.whole and not coalesce(rs.derived, false)) as single_free,
        exists (select from tn where tn.id = cc.id) as turnout,
        cc.stage = 'primary' and (select count(*) from contest_option o where o.contest_id = cc.id and o.kind = 'candidate') >= 2
            as multi,
        tacit and (stage in ('primary', 'runoff') and st in ('AL', 'AR', 'CO', 'CT', 'DE', 'FL', 'KY', 'LA', 'MN', 'MS', 'NC',
            'NY', 'OK', 'SC', 'SD', 'UT', 'VA') or st = 'FL')
        or (select count(*) from contest_option o where o.contest_id = cc.id and o.kind = 'candidate')
            between 1 and coalesce((select k.magnitude from contest k where k.id = cc.id), 1)
            and (stage in ('primary', 'runoff') and st in ('AL', 'AR', 'CT', 'DE', 'FL', 'KY', 'LA', 'MN', 'MS', 'NC', 'NY',
                'OK', 'SC', 'SD', 'UT', 'VA')
                or stage in ('general', 'special') and level in ('state_lower', 'state_upper') and st in ('AR', 'FL', 'HI', 'LA', 'OK', 'TX')
                or stage in ('general', 'special') and level = 'statewide_exec' and st in ('FL', 'LA', 'OK'))
            as cancelled
    from cc left join cg on cg.contest_id = cc.id
)
select st, year, level, stage, count(*), count(*) filter (where results),
    count(*) filter (where county or precinct or cancelled or single), count(*) filter (where precinct or cancelled),
    count(*) filter (where county and not county_free and not coalesce(precinct, false) and not single_free),
    count(*) filter (where turnout), count(*) filter (where not tacit and (multi or stage <> 'primary')),
    count(*) filter (where any_county and not (coalesce(county, false) or coalesce(precinct, false) or coalesce(cancelled, false) or single)),
    count(*) filter (where not results and not coalesce(cancelled, false))
from c group by 1, 2, 3, 4
"""

SHAPES_SIGNATURE_SQL = """
select cc.st, cc.year, cc.level, cc.stage, concat_ws(':', count(*), sum(rs.k), sum(hashtext(cc.id::text)))
from cc join rs on rs.contest_id = cc.id group by 1, 2, 3, 4
"""

SHAPES_SQL = """
select cc.st, cc.year, cc.level, cc.stage, count(distinct ug.k), count(distinct ug.k) filter (where ug.shaped)
from cc join dirty d on d.key = concat_ws('|', cc.st, cc.year, cc.level, cc.stage)
join rs on rs.contest_id = cc.id join rsu on rsu.result_set_k = rs.k join ug on ug.k = rsu.unit_k
where ug.grain = 'precinct'
group by 1, 2, 3, 4
"""

PIPELINES_SQL = """
select distinct cc.st, cc.year, cc.level, cc.stage, p
from cc left join rs on rs.contest_id = cc.id, unnest(array[cc.pipeline, rs.pipeline]) p
where p is not null
"""

STATES_SQL = """
select substr(p.iso_code, 4), ac.code, n.text
from polity p
join polity_territory pt on pt.polity_id = p.id
join area a on a.id = pt.area_id and a.scheme_code = 'us.state'
join area_code ac on ac.area_id = a.id
join name n on n.entity_id = p.id and n.is_preferred
where p.iso_code like 'US-%'
"""

ELECTORS_SQL = """
select distinct extract(year from e.date_from)::int / 4 * 4, substr(p.iso_code, 4)
from contest c
join election e on e.id = c.election_id
join result_set r on r.contest_id = c.id
join result_value v on v.result_set_k = r.k
join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.state'
join polity p on p.id = u.polity_id
where e.kind = 'indirect' and c.kind = 'office' and v.measure = 11
"""

DISTRICTS_SQL = """
select n, substr(ac.code, 1, 2), count(distinct a.id) filter (where substr(ac.code, 3) <> '00'),
    bool_or(substr(ac.code, 3) = '00')
from generate_series(1, 119) n
cross join lateral (select case when n <= 73 then make_date(1787 + 2 * n, 3, 4) else make_date(1787 + 2 * n, 1, 3) end
    as day) d
join area_geometry g on g.valid @> d.day
join area a on a.id = g.area_id and a.scheme_code = 'us.cd'
join area_code ac on ac.area_id = a.id
group by 1, 2
"""

AT_LARGE_SQL = """
select cc.st, cc.year, count(*) from cc
join contest c on c.id = cc.id
join area_code ac on ac.area_id = c.district_area_id and substr(ac.code, 3) = '00'
join area a on a.id = ac.area_id and a.scheme_code = 'us.cd'
where cc.level = 'us_house' and cc.stage = 'general'
group by 1, 2
"""

RUNOFF_SQL = """
create temp table rr(st text, stage text, level text, y0 int, y1 int, thr numeric);

insert into rr values {rules};

create temp table fr as
select cc.id, cc.st, cc.year, cc.level, rr.thr, e.date_from as d,
    c.office_id, c.body_id, c.district_area_id, c.party_scope_id
from cc join contest c on c.id = cc.id join election e on e.id = c.election_id
join rr on rr.st = cc.st and rr.stage = cc.stage and rr.level = cc.level and cc.year between rr.y0 and rr.y1
where c.previous_round_id is null and not cc.tacit;

create temp table nxt as
select n.previous_round_id, ccx.year, ccx.stage from contest n join cc ccx on ccx.id = n.id
where n.previous_round_id is not null;

create index on nxt (previous_round_id);

analyze nxt;

create temp table ro as
select ccx.st, ccx.level, ccx.year, rc.office_id, rc.body_id, rc.district_area_id, rc.party_scope_id, re.date_from as d
from cc ccx join contest rc on rc.id = ccx.id join election re on re.id = rc.election_id
where ccx.stage = 'runoff' and rc.previous_round_id is null;

create index on ro (st, level);

analyze ro;

create temp table fs as
with u as (
    select r.contest_id, r.k, rv.unit_k, rv.option_k,
        coalesce(sum(rv.value) filter (where rv.channel = 0), sum(rv.value)) as v
    from fr join result_set r on r.contest_id = fr.id and r.is_default and r.status <> 'withdrawn'
    join result_value rv on rv.result_set_k = r.k and rv.measure = 1 and rv.option_k <> 0
    group by 1, 2, 3, 4
),
o as (select contest_id, k, option_k, sum(v) as v from u group by 1, 2, 3),
t as (select contest_id, k, max(v) / nullif(sum(v), 0) as top, sum(v) as total, count(*) as n from o group by 1, 2)
select distinct on (contest_id) contest_id, top from t where n >= 2 order by contest_id, total desc;

select fr.st, fr.year, fr.level, fr.thr, fs.top, nx.year, nx.stage, un.year
from fr
left join fs on fs.contest_id = fr.id
left join lateral (
    select nxt.year, nxt.stage from nxt where nxt.previous_round_id = fr.id limit 1
) nx on true
left join lateral (
    select ro.year from ro
    where ro.st = fr.st and ro.level = fr.level and nx.year is null
        and ro.office_id is not distinct from fr.office_id and ro.body_id is not distinct from fr.body_id
        and ro.district_area_id is not distinct from fr.district_area_id
        and ro.party_scope_id is not distinct from fr.party_scope_id
        and ro.d between fr.d + 3 and fr.d + 130
    limit 1
) un on true
where fs.top <= fr.thr and not exists (
        select from result_set r join outcome x on x.result_set_id = r.id and x.kind = 'withdrew'
        where r.contest_id = fr.id and r.is_default
    )
    or nx.year is not null
"""

SPECIALS_SQL = """
select cc.st, cc.level, cc.year, e.date_from
from cc join contest c on c.id = cc.id join election e on e.id = c.election_id
where cc.stage = 'special' and cc.level in ('us_house', 'us_senate')
"""

SENATE_SQL = """
select s.label from seat s join external_id e on e.entity_id = s.body_id and e.scheme = 'us.body' and e.value = 'senate'
"""

LEGISLATIVE_POPULAR_GAPS = {1824: {"DE", "GA", "LA", "NY", "SC", "VT"}, 1828: {"DE", "SC"}, 1864: {"LA"}, 1868: {"FL"}}
SECESSION_STATES = ("AL", "AR", "FL", "GA", "LA", "MS", "NC", "SC", "TX", "TN", "VA")
CLASS_OFFSET = {"I": 2, "II": 4, "III": 0}
GOVERNOR_CYCLES = {
    "midterm": "AL AK AZ CA CO CT FL GA HI ID IL IA KS ME MD MA MI MN NE NV NM NY OH OK OR PA SC SD TN TX WI WY GU VI",
    "presidential": "DE IN MO MT NC ND UT WA WV PR",
    "biennial": "NH VT",
    "virginia": "VA NJ",
    "kentucky": "KY MS LA",
}
GOVERNOR_YEARS = {
    "AR": [*range(1974, 1986, 2), *range(1986, LAST + 1, 4)],
    "RI": [*range(1974, 1994, 2), *range(1994, LAST + 1, 4)],
    "IL": [1976, *range(1978, LAST + 1, 4)],
    "AS": [*range(1980, LAST + 1, 4)],
}
ODD_LEGISLATURES = {
    2023: {"LA": 2019, "MS": 2019, "VA": 2019, ("NJ", "state_lower"): 2019, ("NJ", "state_upper"): 2021},
    2025: {("NJ", "state_lower"): 2021, ("VA", "state_lower"): 2021},
}


def era(year: int) -> str:
    for first, last in ERAS:
        if first <= year <= last:
            return f"{first}–{last}"
    return "other"


def target(st: str, level: str, year: int) -> str:
    if st in ("US", "AS", "GU", "MP", "VI"):
        return "total"
    if year >= 2000:
        return "precinct"
    if year >= 1824 and level not in LOCAL and level not in ("state_upper", "state_lower"):
        return "county"
    return "total"


def owner(level: str, pipelines: set[str]) -> str:
    if any(p.startswith("us.nyc_") for p in pipelines):
        return "nyc lane"
    return "local lane" if level in LOCAL else "coverage lane"


def governor_years(st: str) -> set[int]:
    if st in GOVERNOR_YEARS:
        return set(GOVERNOR_YEARS[st])
    for cycle, states in GOVERNOR_CYCLES.items():
        if st in states.split():
            start = {"midterm": 1974, "presidential": 1976, "biennial": 1974, "virginia": 1977, "kentucky": 1975}[cycle]
            return set(range(start, LAST + 1, 2 if cycle == "biennial" else 4))
    return set()


def matrix(conn: Connection, last: int = LAST) -> list[dict]:
    conn.execute("set statement_timeout = 0")
    for statement in CONTESTS_SQL.split(";\n\n"):
        conn.execute(statement)
    held = {
        (st, year, level, stage): {
            "held": n,
            "results": results,
            "county": county,
            "precinct": precinct,
            "icpsr_only": icpsr,
            "turnout": turnout,
            "contested": contested,
            "partial": partial,
            "bare": bare,
        }
        for st, year, level, stage, n, results, county, precinct, icpsr, turnout, contested, partial, bare in conn.execute(
            COUNT_SQL
        )
    }
    conn.execute(
        "create table if not exists coverage_cache_shapes (key text primary key, signature text not null, "
        "units integer not null, shaped integer not null)"
    )
    shaped_signature = conn.execute(
        "select concat_ws(':', count(*), sum(k::bigint), sum(k::bigint * k)) from ug where grain = 'precinct' and shaped"
    ).fetchone()[0]
    cached = {
        key: (signature, units, shaped)
        for key, signature, units, shaped in conn.execute(
            "select key, signature, units, shaped from coverage_cache_shapes"
        )
    }
    signatures = {
        "|".join(str(part) for part in (st, year, level, stage)): f"{shaped_signature}/{signature}"
        for st, year, level, stage, signature in conn.execute(SHAPES_SIGNATURE_SQL)
    }
    dirty = [key for key, signature in signatures.items() if cached.get(key, ("",))[0] != signature]
    conn.execute("create temp table dirty (key text primary key)")
    conn.cursor().executemany("insert into dirty values (%s)", [(key,) for key in dirty])
    conn.execute("analyze dirty")
    for key in dirty:
        cached[key] = (signatures[key], 0, 0)
    for st, year, level, stage, n, shaped in conn.execute(SHAPES_SQL):
        key = "|".join(str(part) for part in (st, year, level, stage))
        cached[key] = (signatures[key], n, shaped)
    conn.execute("delete from coverage_cache_shapes")
    conn.cursor().executemany(
        "insert into coverage_cache_shapes values (%s, %s, %s, %s)",
        [(key, signature, n, shaped) for key, (signature, n, shaped) in cached.items() if key in signatures],
    )
    shapes = {}
    for key, (_, n, shaped) in cached.items():
        if key in signatures and n > 0:
            st, year, level, stage = key.split("|")
            shapes[(st, int(year), level, stage)] = (n, shaped)
    pipelines: dict[tuple, set[str]] = defaultdict(set)
    for st, year, level, stage, pipeline in conn.execute(PIPELINES_SQL):
        pipelines[(st, year, level, stage)].add(pipeline)
    states = {fips: (st, name) for st, fips, name in conn.execute(STATES_SQL)}
    electors = list(conn.execute(ELECTORS_SQL))
    districts = list(conn.execute(DISTRICTS_SQL))
    at_large = {(st, year): n for st, year, n in conn.execute(AT_LARGE_SQL)}
    seats = [label for (label,) in conn.execute(SENATE_SQL)]

    expected: dict[tuple, int] = defaultdict(int)
    for year, st in electors:
        if year >= 1824 and st not in LEGISLATIVE_POPULAR_GAPS.get(year, set()) and not (st == "SC" and year <= 1860):
            expected[(st, year, "president", "general")] += (
                1 + (2 if st == "ME" and year >= 1972 else 0) + (3 if st == "NE" and year >= 1992 else 0)
            )
    for n, fips, count, statewide in districts:
        if fips in states:
            st, year = states[fips][0], 1786 + 2 * n
            if st == "PR" and year > 1916 and year % 4:
                continue
            expected[(st, year, "us_house", "general")] += count + (
                (1 if count == 0 else at_large.get((st, year), 0)) if statewide else 0
            )
    by_name = {name: st for st, name in states.values()}
    for label in seats:
        name, _, numeral = label.rpartition(" Class ")
        st = by_name[name]
        first = 1960 if st in ("AK", "HI") else 1914
        for year in range(first, LAST + 1):
            if year % 6 == CLASS_OFFSET[numeral]:
                expected[(st, year, "us_senate", "general")] += 1

    for (st, year, level, stage), cell in held.items():
        if level == "governor" and stage == "general" and year < 1974:
            expected[(st, year, level, stage)] = max(expected[(st, year, level, stage)], 1)
    for st, _ in states.values():
        for year in governor_years(st):
            expected[(st, year, "governor", "general")] = 1

    for (st, year, level, stage), cell in list(held.items()):
        if level in ("state_upper", "state_lower") and stage == "general" and 1967 <= year <= 2022:
            expected[(st, year, level, stage)] = max(expected[(st, year, level, stage)], cell["held"])

    def chamber(st: str, year: int, level: str) -> int:
        return held.get((st, year, level, "general"), {}).get("held", 0)

    multi = {
        (st, year, level): n
        for st, year, level, n in conn.execute(
            """select st, year, level, count(*) from cc where stage = 'general'
                and level in ('state_upper', 'state_lower') and year >= 2016
                and (select count(*) from contest_option o where o.contest_id = cc.id and o.kind = 'candidate') >= 2
                group by 1, 2, 3"""
        )
    }

    def proxy(st: str, source: int, year: int, level: str) -> int:
        now = chamber(st, year, level)
        if now and multi.get((st, year, level), 0) * 10 >= now * 9:
            return max(multi.get((st, source, level), 0), now)
        return max(chamber(st, source, level), now)

    for st, _ in states.values():
        for level in ("state_upper", "state_lower"):
            for year, sources in ODD_LEGISLATURES.items():
                source = sources.get((st, level), sources.get(st))
                if source:
                    expected[(st, year, level, "general")] = proxy(st, source, year, level)
            counts = {year: chamber(st, year, level) for year in (2016, 2018, 2020, 2022)}
            if level == "state_lower" and all(counts[y] for y in (2018, 2020, 2022)):
                halves = abs(counts[2020] - counts[2016]) <= counts[2016] / 10 < abs(counts[2022] - counts[2020])
                expected[(st, 2024, level, "general")] = proxy(st, 2020 if halves else 2022, 2024, level)
            staggered = counts[2018] >= counts[2016] / 2 and abs(counts[2020] - counts[2016]) <= counts[2016] / 10
            if level == "state_upper" and all(counts.values()) and staggered:
                expected[(st, 2024, level, "general")] = proxy(st, 2020, 2024, level)
            if level == "state_upper" and st in ("DE", "TX"):
                expected[(st, 2024, level, "general")] = {"DE": 10, "TX": 15}[st]
            if st == "VT":
                for year in range(1968, 1986, 2):
                    expected[(st, year, level, "general")] = chamber(st, 1986, level)
            if st == "NC":
                expected[(st, 1968, level, "general")] = chamber(st, 1970, level)

    for st, year, due in conn.execute(
        """select y.state, y.year, count(*) from statewide_office_year y
            where not y.joint_ticket and not exists (
                select from cc join contest c on c.id = cc.id
                join result_set r on r.contest_id = c.id and r.is_default
                join outcome x on x.result_set_id = r.id and x.kind = 'elected'
                where cc.st = y.state and cc.year = y.year and cc.stage = 'primary' and c.office_id = y.office_id
                    and c.party_scope_id is null
                    and not exists (select from cc g join contest gc on gc.id = g.id
                        where g.st = y.state and g.year = y.year and g.stage = 'general' and gc.office_id = y.office_id))
            group by 1, 2"""
    ):
        key = (st, year, "statewide_exec", "general")
        expected[key] = max(expected[key], due)

    for (st, year, level, stage), cell in list(held.items()):
        if level == "us_senate" and stage == "primary" and year in (2016, 2018):
            expected[(st, year + 6, level, stage)] = max(expected[(st, year + 6, level, stage)], cell["contested"])
        elif level in ("president", "us_house") and stage == "primary" and year in (2020, 2022):
            key = (st, year + 4 if level == "president" else year + 2, level, stage)
            expected[key] = max(expected[key], cell["contested"])

    rules = ", ".join(
        f"('{st}', '{stage}', '{level}', {first}, {end}, {share})"
        for states, stage, levels, first, end, share in RUNOFFS
        for st in states.split()
        for level in levels
    )
    statements = RUNOFF_SQL.replace("{rules}", rules).split(";\n\n")
    for statement in statements[:-1]:
        conn.execute(statement)
    for st, year, level, share, top, next_year, next_stage, loose_year in conn.execute(statements[-1]):
        if next_year is not None and next_stage != "runoff":
            continue
        expected[(st, next_year or loose_year or year, level, "runoff")] += 1

    members = [
        person
        for kind in ("historical", "current")
        for person in json.loads(raw.cached("us.clerk", LEGISLATORS.format(kind=kind)).path.read_text())
    ]
    convenings = {
        1860: date(1861, 3, 4),
        1862: date(1863, 3, 4),
        1864: date(1865, 3, 4),
        1866: date(1867, 3, 4),
        1868: date(1869, 3, 4),
    }
    unrepresented = defaultdict(set)
    for member in members:
        for term in member["terms"]:
            start = date.fromisoformat(term["start"])
            if term["type"] == "rep" and term["state"] in SECESSION_STATES:
                for year, convened in convenings.items():
                    if year < 1868 and convened <= start < convenings[year + 2]:
                        unrepresented[(term["state"], year)].add(member["id"]["bioguide"])
    for st in SECESSION_STATES:
        for year in range(1860, 1868, 2):
            key = (st, year, "us_house", "general")
            if key in expected:
                expected[key] = min(
                    expected[key], max(held.get(key, {}).get("held", 0), len(unrepresented[(st, year)]))
                )

    abbreviations = {st for st, _ in states.values()}
    seated = defaultdict(list)
    for member in members:
        terms = sorted(member["terms"], key=lambda term: term["start"])
        for index, term in enumerate(terms):
            start = date.fromisoformat(term["start"])
            level = "us_senate" if term["type"] == "sen" else "us_house"
            if start.year < (1935 if level == "us_senate" else 1990) or term["state"] not in abbreviations:
                continue
            convened = date(start.year if start.year % 2 else start.year - 1, 1, 3)
            if start < convened:
                convened = date(convened.year - 2, 1, 3)
            continuing = any(
                before["type"] == term["type"] and -1000 <= (start - date.fromisoformat(before["end"])).days <= 30
                for before in terms[:index]
            )
            early = (
                level == "us_senate"
                and start.year % 2 == 0
                and start.month >= 11
                and any(
                    after["type"] == "sen"
                    and 0 <= (date.fromisoformat(after["start"]) - date.fromisoformat(term["end"])).days <= 30
                    for after in terms[index + 1 :]
                )
            )
            if (start - convened).days > 45 and not continuing and not early:
                seated[(term["state"], level, term.get("class"))].append(
                    (start, term.get("how") == "appointment", date.fromisoformat(term["end"]))
                )

    specials = defaultdict(list)
    for st, level, year, day in conn.execute(SPECIALS_SQL):
        specials[(st, level)].append([day, year, False])

    implied: list[tuple[str, str, date, int, date | None]] = []
    for (st, level, seat_class), events in seated.items():
        events.sort()
        groups: list[list[tuple[date, bool, date]]] = []
        for event in events:
            if level == "us_senate" and groups and (event[0] - groups[-1][-1][0]).days <= 900:
                groups[-1].append(event)
            else:
                groups.append([event])
        for group in groups:
            elected = [
                day
                for day, appointed, _ in group
                if not appointed
                and not (level == "us_senate" and day.year % 2 and (day.year - 1) % 6 == {1: 2, 2: 4, 3: 0}[seat_class])
            ]
            if level == "us_house" or elected:
                for day in elected:
                    implied.append((st, level, day, day.year, None))
                continue
            if any(not appointed for _, appointed, _ in group) or group[-1][2] == date(group[-1][2].year, 1, 3):
                continue
            year = group[0][0].year + (1 if group[0][0].year % 2 else 0)
            if group[0][0] >= date(year, 11, 8):
                year += 2
            if year % 6 != {1: 2, 2: 4, 3: 0}[seat_class]:
                implied.append((st, level, date(year, 11, 1), year, group[0][0]))

    for st, level, day, year, since in implied:
        near = [
            hold
            for hold in specials[(st, level)]
            if not hold[2]
            and (
                0 <= (hold[0] - since).days <= 900
                if since is not None
                else abs((hold[0] - day).days) <= 200
                if (day.month, day.day) == (11, 1)
                else -450 <= (hold[0] - day).days <= 60
            )
        ]
        if near:
            hold = min(near, key=lambda hold: abs((hold[0] - day).days))
            hold[2] = True
            year = hold[1]
        expected[(st, year, level, "special")] += 1

    rows: list[dict] = []
    for key in sorted(set(held) | set(expected)):
        st, year, level, stage = key
        if year > last:
            continue
        cell = held.get(
            key,
            {
                "held": 0,
                "results": 0,
                "county": 0,
                "precinct": 0,
                "icpsr_only": 0,
                "turnout": 0,
                "partial": 0,
                "bare": 0,
            },
        )
        exp = expected.get(key)
        base = max(exp or 0, cell["held"])
        goal = target(st, level, year)
        attained = {"precinct": cell["precinct"], "county": cell["county"], "total": cell["results"]}[goal]
        precincts, shaped = shapes.get(key, (0, 0))
        rows.append(
            {
                "state": st,
                "year": year,
                "level": level,
                "stage": stage,
                "expected": "" if exp is None else exp,
                "held": cell["held"],
                "missing": max(0, (exp or 0) - cell["held"]),
                "with_results": cell["results"],
                "county_or_finer": cell["county"],
                "county_partial": cell["partial"],
                "no_results": cell["bare"],
                "precinct": cell["precinct"],
                "with_turnout": cell["turnout"],
                "precinct_share": round(cell["precinct"] / cell["held"], 3) if cell["held"] else 0,
                "grain": "precinct"
                if cell["precinct"]
                else "county"
                if cell["county"] and level not in LOCAL
                else "total"
                if cell["results"]
                else "none",
                "precincts": precincts,
                "shaped_precincts": shaped,
                "shape_share": round(shaped / precincts, 3) if precincts else "",
                "target": goal,
                "gap": base - attained,
                "icpsr_only": cell["icpsr_only"],
                "owner": owner(level, pipelines.get(key, set())),
                "pipelines": " ".join(sorted(pipelines.get(key, set()))),
            }
        )

    return rows


LOCAL_GAPS_SQL = """
with y as (
    select g.id, g.state, g.kind, coalesce(g.population, 0)::bigint as population, g.population_basis, y.held
    from coverage_government g join coverage_government_year y on y.government_id = g.id
    where y.year between 2000 and extract(year from current_date) - 1 and (y.expected or y.held > 0)
)
select state, kind,
    coalesce(sum(population) filter (where held = 0), 0)::bigint,
    sum(population)::bigint,
    coalesce(sum(population) filter (where held > 0), 0)::bigint,
    count(*) filter (where held = 0),
    count(*),
    count(distinct id) filter (where held = 0),
    coalesce(sum(population) filter (where held = 0 and population_basis in ('place', 'county')), 0)::bigint
from y group by 1, 2
"""
LOCAL_LARGEST_SQL = """
select g.state, g.kind, g.name, g.population, count(*)
from coverage_government g join coverage_government_year y on y.government_id = g.id
where y.year between 2000 and extract(year from current_date) - 1 and y.expected and y.held = 0 and g.population > 0
group by g.id, g.state, g.kind, g.name, g.population
order by g.population desc, g.id
"""

LOCAL_GAPS_MD = """# US local gaps

Generated by `ea coverage us` on {today} from the coverage tables built {built} by `ea coverage refresh`. Do not edit by hand. The six local lanes pick their next source from the top of this list.

Every local government in the 2022 Census of Governments that is due to elect (counties, municipalities, townships, school districts, and special districts with elected boards; `pipelines.md`, Coverage) counts once for each year from 2000 through last year in which it was due to vote or the archive holds its contests. Each such election year is weighted by the government's population: the Census Bureau's population estimate for counties, municipalities, and townships (the Government Units List, 2021), and its SAIPE school district population estimates (2022) for school districts. Special districts take the population of the city their name begins with in their county, else of their county; school districts SAIPE does not list take what their county's listed districts leave, and college districts their county's; that weight is estimated, and the Estimated column gives its share of each row's shortfall. Archive polities that match no Census unit carry no population and no calendar, so they count in the raw figures only, in the years they hold contests.

Population short is the population summed over a group's due election years the archive holds none of the government's contests for (residents times elections missed), so a missing mayoral election in a city of 500,000 counts as much as 500 missing elections in towns of 1,000. Rows are ranked by it; the largest governments short list the three most populous with a missed year, their population, and how many due years they miss.

Overall, {weighted} of local election years are held when weighted by population, and {held} of {due} ({share}) are held by count.

{table}
"""


def us() -> str:
    started = time.monotonic()
    with connect() as conn:
        rows = matrix(conn)
    spec = ROOT / ".agents" / "spec"
    with (spec / "us-coverage.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (spec / "us-coverage.md").write_text(report(rows))
    with connect() as conn:
        (built,) = conn.execute("select built_at from coverage_build").fetchone() or (None,)
        groups = conn.execute(LOCAL_GAPS_SQL).fetchall()
        largest: dict[tuple[str, str], list[str]] = defaultdict(list)
        for st, kind, name, population, years in conn.execute(LOCAL_LARGEST_SQL):
            if len(largest[(st, kind)]) < 3:
                largest[(st, kind)].append(f"{name} ({population:,}, {years} short)")
    totals = [sum(row[i] for row in groups) for i in range(2, 9)]
    ranked = sorted((row for row in groups if row[2] > 0), key=lambda row: -row[2])
    held = totals[4] - totals[3]
    (spec / "us-local-gaps.md").write_text(
        LOCAL_GAPS_MD.format(
            today=date.today(),
            built=f"{built:%Y-%m-%d %H:%M}",
            weighted=pct(totals[2], totals[1]),
            held=f"{held:,}",
            due=f"{totals[4]:,}",
            share=pct(held, totals[4]),
            table=table(
                [
                    "Rank",
                    "State",
                    "Government type",
                    "Population short",
                    "Election years short",
                    "Governments short",
                    "Done, weighted",
                    "Estimated",
                    "Largest governments short",
                ],
                [
                    [
                        i,
                        st,
                        kind,
                        f"{short / 1e6:,.1f}M",
                        f"{years:,}",
                        f"{governments:,}",
                        pct(total - short, total),
                        pct(estimated, short),
                        "; ".join(largest[(st, kind)]),
                    ]
                    for i, (st, kind, short, total, _, years, _, governments, estimated) in enumerate(ranked, 1)
                ],
            ),
        )
    )
    return f"{len(rows)} cells, {sum(r['held'] for r in rows)} contests in {time.monotonic() - started:.0f}s"


def table(header: list[str], body: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in body]
    return "\n".join(lines)


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "–"


def top_states(rows: list[dict], field: str, n: int = 6) -> str:
    by_state: dict[str, int] = defaultdict(int)
    for row in rows:
        by_state[row["state"]] += row[field]
    ranked = sorted((v, s) for s, v in by_state.items() if v > 0)[::-1][:n]
    return ", ".join(f"{s} {v:,}" for v, s in ranked)


def report(rows: list[dict]) -> str:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["level"], row["stage"], era(row["year"]), row["target"], row["owner"])].append(row)
    ranked = []
    for (level, stage, span, goal, who), members in groups.items():
        gap = sum(r["gap"] for r in members)
        if gap <= 0:
            continue
        held = sum(r["held"] for r in members)
        missing = sum(r["missing"] for r in members)
        ranked.append(
            (
                gap,
                [level, stage, span, goal, f"{gap:,}", f"{missing:,}", f"{held:,}", top_states(members, "gap"), who],
            )
        )
    ranked.sort(key=lambda r: -r[0])

    levels = []
    for level in LEVELS:
        members = [r for r in rows if r["level"] == level]
        held = sum(r["held"] for r in members)
        if not held and not any(r["expected"] for r in members):
            levels.append([level, 0, "–", "–", "–", "–", "–"])
            continue
        modern = [r for r in members if r["year"] >= 2000]
        older = [r for r in members if 1824 <= r["year"] < 2000]
        modern_held = sum(r["held"] for r in modern)
        older_held = sum(r["held"] for r in older)
        precincts = sum(r["precincts"] for r in modern)
        years = [r["year"] for r in members if r["held"]]
        levels.append(
            [
                level,
                f"{held:,}",
                f"{min(years)}–{max(years)}" if years else "–",
                f"{sum(r['missing'] for r in members):,}",
                pct(sum(r["precinct"] for r in modern), modern_held),
                pct(sum(r["shaped_precincts"] for r in modern), precincts),
                pct(sum(r["county_or_finer"] for r in older), older_held),
            ]
        )

    precinct_years = []
    for year in range(2000, LAST + 1):
        members = [r for r in rows if r["year"] == year and r["level"] not in LOCAL]
        held = sum(r["held"] for r in members)
        precincts = sum(r["precincts"] for r in members)
        cells = [year, f"{held:,}"]
        for level in ("president", "us_senate", "us_house", "governor", "statewide_exec", "state_upper", "state_lower"):
            subset = [r for r in members if r["level"] == level and r["stage"] == "general"]
            cells.append(pct(sum(r["precinct"] for r in subset), sum(r["held"] for r in subset)))
        cells.append(pct(sum(r["shaped_precincts"] for r in members), precincts))
        precinct_years.append(cells)

    missing_rows = []
    for level in ("president", "us_house", "us_senate", "governor", "statewide_exec", "state_upper", "state_lower"):
        for stage in STAGES:
            for first, last in ERAS:
                members = [
                    r for r in rows if r["level"] == level and r["stage"] == stage and first <= r["year"] <= last
                ]
                expected = sum(r["expected"] or 0 for r in members)
                missing = sum(r["missing"] for r in members)
                if missing:
                    missing_rows.append(
                        [
                            level,
                            stage,
                            f"{first}–{last}",
                            f"{expected:,}",
                            f"{missing:,}",
                            top_states(members, "missing"),
                        ]
                    )

    shape_rows = []
    for year in range(2000, LAST + 1):
        members = [r for r in rows if r["year"] == year and r["precincts"]]
        precincts = sum(r["precincts"] for r in members)
        if precincts:
            shaped = sum(r["shaped_precincts"] for r in members)
            unshaped = defaultdict(int)
            for r in members:
                unshaped[r["state"]] += r["precincts"] - r["shaped_precincts"]
            worst = ", ".join(
                f"{s} {v:,}" for v, s in sorted(((v, s) for s, v in unshaped.items() if v), reverse=True)[:8]
            )
            shape_rows.append([year, f"{precincts:,}", pct(shaped, precincts), worst])

    icpsr = defaultdict(int)
    for r in rows:
        if r["icpsr_only"]:
            icpsr[(r["level"], era(r["year"]))] += r["icpsr_only"]

    local = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        if r["owner"] == "local lane" and r["year"] >= 2000:
            local[r["level"]][0] += r["held"]
            local[r["level"]][1] += r["precinct"]
            local[r["level"]][2] += r["gap"]

    today = date.today().isoformat()
    total = sum(r["held"] for r in rows)
    return f"""# US coverage

Generated by `ea coverage us` on {today} from the database, measured against the US v1 goal in `us-v1-plan.md`. Do not edit by hand; rerun the command after each fill. `us-coverage.csv` holds the full matrix: one row per state (or `US` for national contests), year, office level, and stage, with contests held and expected, the finest grain held, the share with precinct results, the share of those precincts with shapes, the target grain, the gap, the owning lane, and the pipelines.

{total:,} US contests held.

## How it is measured

- Level comes from the contest's office or body and its polity: the president (state slates, national contests, Electoral College votes, and primary delegate allocations), the US Senate and House, governors, other statewide offices (`judicial` when the office names a justice, judge, or court), state upper and lower chambers (Nebraska's unicameral counts as upper), county, municipal, and school district offices and bodies (county and city offices naming a school or education board count as school), and statewide and local ballot questions. No special district polities exist yet, so that level is empty.
- Stage: `runoff` for runoff elections (general and primary), `primary` for party-scoped contests and primary, top-two, and caucus elections, `special` for special elections and remainder terms on a regular day, and `general` otherwise.
- Grain: a contest is `precinct` when any of its result sets has a precinct unit (`us.precinct`, `us.nyc_ed`, Iowa caucus precincts), `county` when any has a county, town, county part, Alaska house district, or place unit, `total` when it has any result set, and `none` otherwise. A U.S. House or state legislative contest (multi-member districts included) with results whose district shape on the election date lies at least 98% inside one county shape of that date, or at least half inside one with no other county shape taking more than 2% (the rest is water the county shapes leave out), counts as `county`, since its district total is that county's return (a district with no shape dated for the election does not qualify); an Alaska State House contest counts the same way, since its district is the Alaska house district unit that stands in for counties (`district_county` caches the overlay: the county under the district's point on surface, by shape vintage, with `rest` the largest share another county takes). A precinct has a shape when its unit equals an area with geometry. `icpsr_only` counts contests whose only county-level results come from ICPSR or from Algara and Amlani's returns, which are compiled from ICPSR and CQ (`us.icpsr`, `us.algara`, `us.algara_federal`; the ICPSR replacement lane's work). A result set is matched by its source's publisher, not by the changeset that last wrote it, since another pipeline that makes a set non-default restages it.
- Target: precinct from 2000, county from 1824 to 1999 (total for local offices, local measures, and state legislative districts, whose county breakdowns before 2000 are not measured), and the contest total before 1824 and in the territories that have no counties (American Samoa, Guam, the Northern Mariana Islands, and the Virgin Islands). `gap` is the larger of contests expected and held, less those reaching the target. A primary or runoff listed as unopposed with no vote (the FEC's `Unopposed`, held as a tacit result), or with no more candidates than seats outside Colorado, needs no breakdown in the states whose law cancels an uncontested primary and declares the lone candidate nominated: Alabama, Arkansas, Colorado (when the whole primary is canceled), Connecticut, Delaware, Florida, Kentucky, Louisiana, Minnesota, Mississippi, New York, North Carolina, Oklahoma, South Carolina, South Dakota, Utah, and Virginia. Neither does a Florida contest held as a tacit result, or a state legislative general or special in Arkansas, Florida, Hawaii, Louisiana, Oklahoma, or Texas with no more candidates than seats, or a statewide executive general or special in Florida, Louisiana, or Oklahoma with one candidate, where an unopposed candidate is left off the ballot and declared elected with no vote (Ark. Code § 7-5-207, Fla. Stat. 101.151(7), Haw. Rev. Stat. §12-41(a), Texas Election Code §2.056). A result set whose completeness is below 0.98 (a county sum short of the official total, or an unfinished count) reaches neither county nor precinct grain, and its contest counts as partial, unless every named candidate's votes in it equal that candidate's votes in the contest's default result set, so only write-in or scattered votes differ.
- Expected contests: presidential slates for each state casting electoral votes from 1824 (less the states whose legislatures chose electors, and Maine's and Nebraska's districts), House seats from `us.cd` districts in force at each Congress's start, with a state's at-large seat counted only when it has no numbered districts or a source holds an at-large race that cycle (each race filed under the even year before the Congress its winner was seated in, or, with no seated winner, the Congress its date falls in), regular Senate elections from each state's seat classes since 1914 (1960 in Alaska and Hawaii), governors from 1974 by each state's cycle and before 1974 wherever any source holds one, state legislative generals as Klarner holds them 1967–2022 (Vermont 1968–1984 and North Carolina 1968, which Klarner lacks, at their next counts) and 2023–2025 at the count four years earlier (2024 lower houses at 2022's count, or 2020's where 2016 and 2020 match and 2022, a redistricting year, differs by more than a tenth, as in North Dakota's staggered House; Delaware's and Texas's 2024 senates at the 10 and 15 seats those states put up after redistricting; or, where at least nine in ten contests held that year have two or more candidates, as in Florida, Hawaii, Louisiana, and Oklahoma, whose sources hold only contested races, the earlier year's contests with two or more candidates), other statewide offices from the `statewide_office_year` table (one contest for each office a state has elected in at least two general elections since 1990, in every year of its cycle from the first year its holders are known, or from the state's first governor election, and before 1990 in the years the state elected its governor when the office's modern cycle matches the governor's; lieutenant governors on the governor's ticket have no contest of their own and are not counted, and an office won outright in a nonpartisan primary, as California's superintendent of public instruction can be, expects no general; Georgia's Public Service Commission, whose 2022 and 2024 elections the courts postponed, is expected in 2025, and Colorado's State Board of Education and University of Colorado regents, elected mostly by congressional district with an at-large seat only in some years, count only what is held; North Dakota's attorney general, secretary of state, agriculture commissioner, and tax commissioner were elected in presidential years through 2004 and in midterm years from 2006, so 1994, 1998, and 2002 expect none; and Indiana's superintendent of public instruction, appointed from 2021, was last elected in 2016), and 2024 House and presidential primaries at the larger of their 2022 and 2020 counts of contests, and 2024 Senate primaries (the 2018 seat class) at the 2018 count of contests with two or more candidates and a counted result, since 2024's sources hold only contested primaries. Runoffs are expected where a first round we hold leaves no candidate above its state's threshold (see below), and House and Senate specials where a member's term begins mid-term. Judicial races, ballot measures, local races, other specials, and anything before these windows have no calendar, so they count only what is held.
- Runoffs: the rule table in `coverage.RUNOFFS` gives each state's first-round stage, offices, years, and threshold: a majority in Alabama, Arkansas, Georgia, Mississippi, Oklahoma, South Carolina, and Texas primaries, Florida's primaries through 2000, South Dakota's 35 percent for Congress and governor, Georgia's general elections from 1964, Louisiana's majority elections (statewide and legislative from 1975, Congress 1978–2006 and from 2012), and the jungle specials of Georgia, Louisiana, Mississippi, and Texas. Each first round (a contest with no earlier round) whose default result set has a top vote share at or below the threshold, or which already has a runoff chained to it, expects one runoff, filed under the year of the runoff held (chained, or found by office and district within 130 days) and otherwise the first round's year. A first round chained to a contest that is not stage `runoff` (Louisiana's November generals) adds nothing. North Carolina's second primary is held only when the runner-up asks for one (N.C. Gen. Stat. § 163-111), so its runoffs count only what is held. A first round whose default result set has a `withdrew` outcome (the runner-up withdrew and the leader was declared elected) expects no runoff unless one is held.
- Secession: for the eleven Confederate states, a House cell for the Congresses of 1860 through 1866 expects at most as many contests as the Clerk's Biographical Directory (congress-legislators' terms) seats members from that state starting between that Congress's convening and the next (the state's delegation was absent during secession, and readmitted states seated members from 1868), or as many as we hold if more.

- Specials: from congress-legislators' terms. A House term from 1990 or a Senate term from 1935 that starts more than 45 days after the Congress convened, with no term of the same chamber ending within 30 days before it, implies one special (a Senate term that begins in November or December of an even year and runs straight into the same member's next term is an early swearing-in after a resignation, and implies none), held in the cell of the matching special we hold (election within 450 days before the swearing-in) or else the swearing-in year. A Senate appointment implies a special in the first even year after it unless that year is the seat class's regular election; an appointee and the later winner are one special. Earlier terms start on irregular dates in the source, so older specials have no calendar. Governors' and statewide officers' tenures show every elected successor on cycle (two exceptions), so they imply none.

## Coverage by level

Contests held, years spanned, contests missing against the calendar, the share of contests from 2000 with precinct results, the share of those precincts with shapes, and the share of 1824–1999 contests with county or finer results.

{table(["Level", "Contests", "Years", "Missing", "Precinct, 2000+", "Shaped", "County, 1824–1999"], levels)}

## Gaps ranked by size

Each row groups the matrix by level, stage, era, target, and owner. Gap is contests short of the target grain (missing contests included); missing is contests the calendar expects that no source holds.

{table(["Level", "Stage", "Era", "Target", "Gap", "Missing", "Held", "Largest states", "Owner"], [r for _, r in ranked[:60]])}

## Missing contests against the calendar

{table(["Level", "Stage", "Era", "Expected", "Missing", "Largest states"], missing_rows)}

## Precinct results, 2000 on

Contests held at state and federal levels per year, the share of general-election contests with precinct results per level, and the share of those years' precincts with shapes.

{table(["Year", "Contests", "President", "Senate", "House", "Governor", "Statewide", "Upper", "Lower", "Shaped"], precinct_years)}

## For other lanes

Precinct shapes (the precinct shapes lane): precincts with results and the share with shapes, with the states holding the most precincts without one.

{table(["Year", "Precincts", "Shaped", "Most unshaped"], shape_rows)}

Local offices and measures from 2000 (the local lane): contests held, with precinct results, and gap to precinct grain.

{table(["Level", "Held", "Precinct", "Gap"], [[k, f"{v[0]:,}", f"{v[1]:,}", f"{v[2]:,}"] for k, v in sorted(local.items())])}

ICPSR-only county results (the ICPSR replacement lane): contests whose only county breakdown comes from `us.icpsr`, `us.algara`, or `us.algara_federal`.

{table(["Level", "Era", "Contests"], [[lv, span, f"{v:,}"] for (lv, span), v in sorted(icpsr.items())])}
"""
