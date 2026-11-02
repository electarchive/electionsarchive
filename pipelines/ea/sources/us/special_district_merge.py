import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.sources.us import enr_clarity
from ea.sources.us.enr_clarity_twins import LIVE, _person
from ea.store import connect

SOURCE = "us.special_district_merge"
VERSION = "9"
GENERIC = re.compile(
    r"\b(?:no|number|ward|wd|district|dist|subdistrict|subdivision|protection|fire|water|sewer|sewerage|drainage|drain|"
    r"gravity|grav|road|rd|lighting|light|recreation|recreational|hospital|hosp|service|services|ser|serv|svc|area|"
    r"waterworks|utility|utilities|municipal|special|public|improvement|community|development|conservation|soil|and|"
    r"of|the|on|consolidated|consolidatedschool|con|parish|county|emergency|communications|levee|library|cemetery|"
    r"park|school|schooldistrict|college|board|watershed|irrigation|port|transit|economic|tax|sales|mosquito|"
    r"abatement|abate|control|natural|resources?|airport|authority|metropolitan|regional|rural|unified|independent|"
    r"police|law|enforcement|health|ambulance|commission|comm|zone|council|aging|election|prop|garbage|educational|"
    r"facilities|one|two|three|four|five|six|seven|eight|nine|ten|first|second|third|fourth|fifth|sixth|seventh|"
    r"eighth|ninth|tenth|fifteen|gdd|fpd|sd|mud|wcid|esd|fwsd|sud|cdd|pud|par|sch|\d+[a-z]?|[a-z])\b"
)
ALIASES = {
    ("17", "foxmetrowaterdistrict4"): "foxmetrowaterreclamationdistrict",
    ("48", "trophyclubmunicipalutilitydistrict"): "trophyclubmunicipalutilitydistrict1",
    ("48", "ftbendwatercontrolandimprovementdistrict2"): "fortbendwatercontrolandimprovementdistrict2",
    ("48", "bastoptraviscoemergencyservicesdistrict1"): "bastroptravisemergencyservicesdistrict1",
    ("06", "sjdeltacommunitycollegedistrict"): "sanjoaquindeltacommunitycollegedistrict",
    ("20", "wyandottecountywaterdistrict1"): "jocowaterdistrict1",
    ("26", "gratiotcountycommunitycollegedistrict"): "montcalmcommunitycollegedistrict",
    ("29", "saintlouiscommunitycollegedistrict"): "stlouiscommunitycollegedistrict",
}
RETIRED = "A misread district that lumped several districts together; each district now has its own polity"
UNRELATED = {"increasesfundingsourcespublicschoolscommunitycollegedistrict"}
MISREAD = {
    "special:17043:fire:bloomingdale-emergency-services-district-13": "Bloomingdale's ESD 13 is Bloomingdale School "
    "District 13, an elementary school district, not an emergency services district",
    "special:48085:college:austin-community-college-district": "Collin County's 1999 line on casualty and accident "
    "insurance (Acc Ins) is a state constitutional amendment, not an Austin Community College District question",
    "special:06059:college:increases-funding-sources-public-schools-community-college-district": "Orange County's "
    "2020 line for Proposition 15 (funding for public schools, community colleges, and local services) is a statewide "
    "measure, not a community college district question",
    "special:06067:college:increases-funding-sources-public-schools-community-college-district": "Sacramento "
    "County's 2020 line for Proposition 15 (funding for public schools, community colleges, and local services) is a "
    "statewide measure, not a community college district question",
}
PREFERRED = "(select text from name n where n.entity_id = {0} and n.is_preferred and n.lang = 'en' limit 1)"


def _folded(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _question(title: str | None) -> str:
    return _folded(re.sub(r"\bno\b", "", (title or "").partition(": ")[2].lower()))


def emit() -> Path:
    family = enr_clarity.family(SOURCE)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge special districts written once per county into one polity, and fold contests that the county election night pipelines re-keyed under a corrected district into their new copies",
            polity="US",
            kind="correction",
        )
        rows = conn.execute(
            f"""with stale as (
                  select x.id, x.kind, x.round_number, x.party_scope_id, x.body_id, x.office_id,
                         e.date_from, e.polity_id, g.pipeline
                  from contest x join election e on e.id = x.election_id join changeset g on g.id = x.changeset_id
                  where g.pipeline = any(%s) and {LIVE.format("x")}
                    and exists (select from result_set r where r.contest_id = x.id)
                    and not exists (select from result_set r where r.contest_id = x.id and r.status <> 'withdrawn')
                )
                select s.id, s.polity_id, s.body_id, s.office_id, o.id, f.polity_id, o.body_id, o.office_id,
                       split_part(a.value, ':', 2) = split_part(b.value, ':', 2),
                       {PREFERRED.format("s.id")}, {PREFERRED.format("o.id")},
                       (select array_agg(y.ballot_name) from contest_option p join candidacy y on y.option_id = p.id
                        where p.contest_id = s.id and y.ballot_name is not null),
                       (select array_agg(y.ballot_name) from contest_option p join candidacy y on y.option_id = p.id
                        where p.contest_id = o.id and y.ballot_name is not null)
                from stale s
                join election f on f.date_from = s.date_from
                join contest o on o.election_id = f.id and o.id <> s.id and o.kind = s.kind
                  and o.round_number is not distinct from s.round_number
                  and o.party_scope_id is not distinct from s.party_scope_id and {LIVE.format("o")}
                join changeset h on h.id = o.changeset_id and h.pipeline = any(%s)
                join external_id a on a.entity_id = s.polity_id and a.scheme = 'us.polity'
                join external_id b on b.entity_id = f.polity_id and b.scheme = 'us.polity'
                  and left(split_part(a.value, ':', 2), 2) = left(split_part(b.value, ':', 2), 2)
                where exists (select from result_set r where r.contest_id = o.id and r.status = 'active')""",
            [[*family, "us.medsl_local"], family],
        ).fetchall()
        matches: dict[
            UUID, set[tuple[UUID, UUID, UUID, UUID | None, UUID | None, UUID | None, UUID | None, bool, int]]
        ] = defaultdict(set)
        counted: dict[UUID, list[tuple[int, int, int, Decimal]]] = {}

        def votes(contest: UUID) -> list[tuple[int, int, int, Decimal]]:
            if contest not in counted:
                counted[contest] = conn.execute(
                    """select r.k, v.unit_k, v.measure, v.value from result_value v join result_set r on r.k = v.result_set_k
                       where r.contest_id = %s and r.status in ('active', 'withdrawn')""",
                    [contest],
                ).fetchall()
            return counted[contest]

        def tally(contest: UUID) -> set[tuple[int, int, Decimal]]:
            return {(unit, measure, value) for _, unit, measure, value in votes(contest)}

        def fingerprint(contest: UUID) -> set[tuple[int, tuple[Decimal, ...]]]:
            units: dict[tuple[int, int, int], list[Decimal]] = defaultdict(list)
            for result, unit, measure, value in votes(contest):
                units[(result, unit, measure)].append(value)
            return {(measure, tuple(sorted(values))) for (_, _, measure), values in units.items()}

        for (
            stale,
            polity,
            body,
            office,
            other,
            survivor,
            other_body,
            other_office,
            local,
            title,
            other_title,
            ours,
            theirs,
        ) in rows:
            mine = {person for person in map(_person, ours or []) if person}
            held = {person for person in map(_person, theirs or []) if person}
            shared = len(mine & held)
            if mine or held:
                tier = 3 if shared * 2 > len(mine) and shared * 2 > len(held) else 0
            elif _question(title) == _question(other_title) != "":
                tier = 3
            elif tally(stale) and tally(stale) <= tally(other):
                tier = 2
            elif local and fingerprint(stale) and fingerprint(stale) <= fingerprint(other):
                tier = 1
            else:
                tier = 0
            if tier:
                matches[stale].add((other, polity, survivor, body, other_body, office, other_office, local, tier))
        chosen: dict[UUID, tuple[UUID, UUID, UUID, UUID | None, UUID | None, UUID | None, UUID | None, bool, int]] = {}
        for stale, found in sorted(matches.items()):
            best = max(match[8] for match in found)
            found = {match for match in found if match[8] == best}
            if len({match[0] for match in found}) > 1:
                found = {match for match in found if match[7]}
            if len({match[0] for match in found}) == 1:
                chosen[stale] = next(iter(found))
        rekeyed: dict[UUID, set[UUID]] = defaultdict(set)
        for other, polity, survivor, body, other_body, office, other_office, *_ in chosen.values():
            if polity != survivor:
                rekeyed[polity].add(survivor)
            for owner, target in ((body, other_body), (office, other_office)):
                if owner is not None and target is not None and owner != target:
                    rekeyed[owner].add(target)
        owners = list(rekeyed)
        kept = {
            entity
            for (entity,) in conn.execute(
                f"""select o.id from unnest(%s::uuid[]) o(id)
                    where exists (select from contest k join election e on e.id = k.election_id
                                  where o.id in (e.polity_id, k.body_id, k.office_id) and {LIVE.format("k")}
                                    and not (k.id = any(%s::uuid[])))
                       or exists (select from polity p where p.id = o.id)
                          and not exists (select from external_id x where x.entity_id = o.id and x.scheme = 'us.polity'
                                            and x.value like 'special:%%')""",
                [owners, list(chosen)],
            ).fetchall()
        }
        orphans = {
            polity
            for (polity,) in conn.execute(
                "select x.entity_id from external_id x where x.entity_id = any(%s) and x.scheme = 'us.polity' and not (x.value = any(%s))",
                [[entity for entity in owners if len(rekeyed[entity]) > 1 and entity not in kept], list(MISREAD)],
            ).fetchall()
        }
        retired: set[UUID] = set()
        for stale, (other, polity, *_) in chosen.items():
            if polity in orphans:
                retired.add(stale)
                cs.add("entity_retirement", entity_id=stale, reason=RETIRED)
            else:
                cs.merge(stale, other)
        involved: set[UUID] = set()
        for entity, targets in sorted(rekeyed.items()):
            if len(targets) == 1 and entity not in kept and entity not in orphans:
                cs.merge(entity, next(iter(targets)))
                involved |= {entity, *targets}
        for query in (
            f"select e.id from election e where e.polity_id = any(%s) and {LIVE.format('e')}",
            f"select o.id from office o where o.polity_id = any(%s) and {LIVE.format('o')}",
            f"select b.id from body b where b.polity_id = any(%s) and {LIVE.format('b')}",
            f"select u.id from reporting_unit u where u.polity_id = any(%s) and {LIVE.format('u')}",
            "select p.id from polity p where p.id = any(%s)",
        ):
            for (entity,) in conn.execute(query, [list(orphans)]).fetchall():
                if entity not in cs.survivors and entity not in retired:
                    retired.add(entity)
                    cs.add("entity_retirement", entity_id=entity, reason=RETIRED)
        for unit, area in conn.execute(
            """select x.unit_id, x.area_id from reporting_unit_area x
               where x.area_id in (select area_id from polity_territory where polity_id = any(%s)
                                   union select area_id from reporting_unit where polity_id = any(%s))""",
            [list(orphans)] * 2,
        ).fetchall():
            cs.add("unit_area_drop", unit_id=unit, area_id=area, reason=RETIRED)

        named: dict[tuple[str, str], list[tuple[UUID, str, int, str]]] = defaultdict(list)
        for polity, value, text, contests in conn.execute(
            f"""select distinct on (p.id) p.id, x.value, {PREFERRED.format("p.id")},
                       (select count(*) from election e join contest k on k.election_id = e.id where e.polity_id = p.id)
                from polity p join external_id x on x.entity_id = p.id and x.scheme = 'us.polity'
                where x.value like 'special:%%' and {LIVE.format("p")}
                order by p.id, x.value"""
        ).fetchall():
            if polity not in cs.survivors and polity not in retired and value not in MISREAD:
                county = value.split(":")[1]
                folded = _folded(text)
                named[(county[:2], ALIASES.get((county[:2], folded), folded))].append(
                    (polity, county, contests, text or "")
                )
        groups = 0
        for (_, folded), members in sorted(named.items()):
            if len(members) < 2 or folded in UNRELATED:
                continue
            if len({member[1] for member in members}) > 1 and not all(
                GENERIC.sub(
                    " ", re.sub(r"[^a-z0-9 ]", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", member[3]).lower())
                ).split()
                for member in members
            ):
                continue
            survivor = max(members, key=lambda member: (_folded(member[3]) == folded, member[2], str(member[0])))[0]
            groups += 1
            for member in members:
                cs.merge(member[0], survivor)
                involved.add(member[0])

        def fold(rows: list[tuple[UUID, tuple[object, ...], tuple[bool, int]]]) -> None:
            grouped: dict[tuple[object, ...], list[tuple[UUID, tuple[bool, int]]]] = defaultdict(list)
            for entity, key, weight in rows:
                if entity not in cs.survivors:
                    grouped[key].append((entity, weight))
            for members in grouped.values():
                survivor = max(members, key=lambda member: (member[1], str(member[0])))[0]
                for entity, _ in members:
                    cs.merge(entity, survivor)

        ids = [list(involved)]
        fold(
            [
                (election, (cs.survivors.get(polity, polity), day, kind), (polity not in cs.survivors, contests))
                for election, polity, day, kind, contests in conn.execute(
                    f"""select e.id, e.polity_id, e.date_from, e.kind::text,
                               (select count(*) from contest k where k.election_id = e.id)
                        from election e where e.polity_id = any(%s) and {LIVE.format("e")}""",
                    ids,
                ).fetchall()
            ]
        )
        fold(
            [
                (
                    owner,
                    (table, cs.survivors.get(polity, polity), kind, _folded(text)),
                    (polity not in cs.survivors, contests),
                )
                for owner, table, polity, kind, text, contests in conn.execute(
                    f"""select b.id, 'body', b.polity_id, b.kind::text, {PREFERRED.format("b.id")},
                               (select count(*) from contest k where k.body_id = b.id)
                        from body b where b.polity_id = any(%s) and {LIVE.format("b")}
                        union all
                        select o.id, 'office', o.polity_id, o.kind::text, {PREFERRED.format("o.id")},
                               (select count(*) from contest k where k.office_id = o.id)
                        from office o where o.polity_id = any(%s) and {LIVE.format("o")}""",
                    ids * 2,
                ).fetchall()
            ]
        )
        for value, reason in MISREAD.items():
            for (entity,) in conn.execute(
                """select k.id from external_id x join election e on e.polity_id = x.entity_id
                   join contest k on k.election_id = e.id where x.scheme = 'us.polity' and x.value = %s
                   union all select e.id from external_id x join election e on e.polity_id = x.entity_id
                   where x.scheme = 'us.polity' and x.value = %s
                   union all select u.id from external_id x join reporting_unit u on u.polity_id = x.entity_id
                   where x.scheme = 'us.polity' and x.value = %s
                   union all select x.entity_id from external_id x where x.scheme = 'us.polity' and x.value = %s""",
                [value] * 4,
            ).fetchall():
                cs.add("entity_retirement", entity_id=entity, reason=reason)
        elections = conn.execute(
            f"""select distinct on (e.id) e.id, e.polity_id, n.id, n.text from election e
                join external_id x on x.entity_id = e.polity_id and x.scheme = 'us.polity' and x.value like 'special:%%'
                  and not (x.value = any(%s))
                join name n on n.entity_id = e.id and n.is_preferred and n.lang = 'en'
                join changeset g on g.id = e.changeset_id
                where g.pipeline = any(%s) and {LIVE.format("e")}""",
            [list(MISREAD), family],
        ).fetchall()
        polities = dict(
            conn.execute(
                "select entity_id, text from name where entity_id = any(%s) and is_preferred and lang = 'en'",
                [list({cs.survivors.get(polity, polity) for _, polity, _, _ in elections})],
            ).fetchall()
        )
        renamed = 0
        for election, polity, name, text in elections:
            styled = re.fullmatch(r"(\d{4}) (.+) (\w+ elections?)", text)
            place = polities.get(cs.survivors.get(polity, polity))
            if (
                election in cs.survivors
                or election in retired
                or styled is None
                or place is None
                or _folded(styled[2]) == _folded(place)
            ):
                continue
            renamed += 1
            reason = f"{styled[2]} is a misread of the district's name, {place}"
            cs.add("name_drop", name_id=name, entity_id=election, text=text, reason=reason)
            cs.add(
                "name",
                entity_id=election,
                text=f"{styled[1]} {place} {styled[3]}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        print(
            f"{len(rekeyed)} re-keyed owners, {len(orphans)} lumped districts retired, {groups} special districts written once per county, {renamed} elections renamed"
        )
        return cs.write()
