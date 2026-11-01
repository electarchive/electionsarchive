import csv
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import local_2026
from ea.store import connect

SOURCE = "us.mi_judicial_2026"
VERSION = "1"
DAY = date(2026, 11, 3)
ROWS = Path(__file__).with_name("mi_judicial_2026.tsv")
RAW = "us.local_2026"
LISTING = "https://mi-boe.entellitrak.com/etk-mi-boe-prod/page.request.do?page=page.miboePublicReport&electionType=GEN&electionYear=2026"
STALE = "Superseded by the statewide candidate listing's seat and incumbency labels"
OFFICES = {
    "circuit": "Judge of Circuit Court",
    "district": "Judge of District Court",
    "probate": "Judge of Probate Court",
}


def read() -> list[dict[str, str]]:
    with ROWS.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def ordinal(number: str) -> str:
    match = re.fullmatch(r"(\d+)([A-Z]?)", number)
    if match is None:
        return number
    value = int(match[1])
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(value if value < 20 else value % 10, "th")
    return f"{value}{match[2]}" if match[2] else f"{value}{suffix}"


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st "))


def emit() -> Path:
    rows = read()
    blocks: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        blocks[(row["court"], row["number"], row["seat"], row["position"], row["term"])].append(row)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan circuit, district, and probate court judges on the November 3, 2026 ballot, from county clerks' candidate lists",
            polity="US",
        )
        names = {
            code: text.removesuffix(", MI")
            for code, text in conn.execute(
                """select ac.code, n.text from area a join area_code ac on ac.area_id = a.id
                   join name n on n.entity_id = a.id and n.text like '%% County, MI'
                   where a.scheme_code = 'us.county' and ac.code like '26%%'"""
            ).fetchall()
        }
        labels = {v[0]: (v[1], v[2]) for k, v in local_2026.FEEDS.items() if k.startswith("MI-")}
        labels[LISTING] = (
            "Michigan Department of State, Bureau of Elections",
            "Official Candidate Listing, All State and Judicial Offices, November 3, 2026 general election",
        )
        for key, url in local_2026.FILES.items():
            if key.startswith("MI-") and url not in labels:
                labels[url] = (
                    f"{names['26' + key[5:8]]} Clerk",
                    "Candidate list for the November 3, 2026 general election",
                )
        sources: dict[str, UUID] = {}
        for url in sorted({row["source"] for row in rows}):
            publisher, title = labels[url]
            sources[url] = cs.source(publisher, title)
            if sources[url] in cs.minted:
                cs.add(
                    "source",
                    id=sources[url],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license=None,
                )
            cs.artifact(raw.cached(RAW, url), sources[url])

        found = conn.execute(
            """select u.area_id, p.id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-MI'"""
        ).fetchone()
        if found is None:
            raise LookupError("no Michigan state unit")
        state_area, state_polity = found
        election = cs.key("us.election", f"{DAY}:MI")
        if election in cs.minted:
            raise LookupError("no 2026-11-03 election for MI")
        counties = {flat(text.removesuffix(" County")): code for code, text in names.items()}
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/mi/%%'").fetchall()}
        fptp = cs.key("us.electoral_system", "fptp")
        block = cs.key("us.electoral_system", "block")

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        districts: dict[tuple[str, str], tuple[UUID, UUID]] = {}
        for court, number in sorted({(key[0], key[1]) for key in blocks if key[0] != "probate"}):
            kind = "circuit" if court == "circuit" else "district"
            code = f"MI:{kind}-{number}"
            label = (
                f"{ordinal(number)} Judicial Circuit"
                if court == "circuit"
                else f"{ordinal(number)} District Court District"
            )
            area = cs.area("us.judicial_district", code)
            polity = cs.key("us.polity", f"judicial:{code}")
            districts[(court, number)] = (polity, area)
            if polity not in cs.minted:
                continue
            path = f"us/mi/{slug(label)}"
            if path in taken:
                raise ValueError(f"{label} would take {path}")
            taken.add(path)
            cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
            name(polity, f"Michigan {label}")
            cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
            cs.add("slug", path=path, entity_id=polity)
            cs.add("area", id=area, scheme_code="us.judicial_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=state_area)
            name(area, f"{label}, MI")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "reporting_unit",
                id=cs.unit("us.judicial_district", code),
                polity_id=polity,
                scheme_code="us.judicial_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )

        offices: dict[str, UUID] = {}
        for court, title in OFFICES.items():
            offices[court] = cs.key("us.office", f"{slug(title)}:MI")
            if offices[court] in cs.minted:
                cs.add("office", id=offices[court], polity_id=state_polity, kind="other")
                name(offices[court], f"{title}, MI")

        persons: set[UUID] = set()
        contests: set[UUID] = set()
        made = candidates = 0
        for (court, number, seat, position, term), members in sorted(blocks.items()):
            seats = int(members[0]["seats"])
            if court == "probate":
                area = cs.area("us.county", counties[flat(number)])
                place = f"{number} County"
            else:
                area = districts[(court, number)][1]
                place = f"{ordinal(number)} {'Circuit' if court == 'circuit' else 'District'}"
            partial = term.startswith("partial")
            qualifiers = [seat, position.replace("non-incumbent", "open seat"), "partial term" if partial else ""]
            tag = ", ".join(text for text in qualifiers if text and text != "incumbent")
            label = f"{OFFICES[court]}, {place}" + (f" ({tag})" if tag else "")
            key = f"{DAY}:MI:{slug(OFFICES[court])}:{slug(place)}:{slug(seat)}:{slug(position)}:{slug(term)}"
            contest = cs.key("us.contest", key)
            contests.add(contest)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=offices[court],
                body_id=None,
                district_area_id=area,
                seat_id=None,
                boundary_set_id=None,
                magnitude=seats,
                term="remainder" if partial else "full",
                round_number=1,
                previous_round_id=None,
                electoral_system_id=block if seats > 1 else fptp,
                default_result_kind="counted",
            )
            name(contest, f"2026 MI {label}")
            base = f"us/mi/2026-11-03-general/{slug(f'{OFFICES[court]} {place} {seat} {position} {term if partial else ""}')}"
            path, count = base, 1
            while path in taken:
                count += 1
                path = f"{base}-{count}"
            taken.add(path)
            cs.add("slug", path=path, entity_id=contest)
            for url in sorted({row["source"] for row in members}):
                cs.add("citation", entity_id=contest, source_id=sources[url], locator=None)
            made += 1
            for position_number, row in enumerate(members, start=1):
                text = row["candidate"]
                option = cs.key("us.option", f"{key}:{slug(text)}")
                person = cs.key(f"{SOURCE}.person", f"{key}:{slug(text)}")
                if person not in persons:
                    persons.add(person)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, text)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_position=position_number,
                    ballot_label=text,
                )
                candidacy = cs.key("us.candidacy", f"{key}:{slug(text)}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=text,
                    is_independent=False,
                )
                cs.add("citation", entity_id=candidacy, source_id=sources[row["source"]], locator=None)
                candidates += 1
        for (stale,) in conn.execute(
            """select c.id from contest c join changeset ch on ch.id = c.changeset_id
               join entity e on e.id = c.id
               where ch.pipeline = %s and e.retired_by is null and not c.id = any(%s)""",
            [SOURCE, list(contests)],
        ).fetchall():
            cs.add("entity_retirement", entity_id=stale, reason=STALE)
            for (orphan,) in conn.execute(
                """select distinct cy.person_id from candidacy cy join contest_option o on o.id = cy.option_id
                   where o.contest_id = %s""",
                [stale],
            ).fetchall():
                if orphan not in persons:
                    cs.add("entity_retirement", entity_id=orphan, reason=STALE)
        print(made, "contests,", candidates, "candidates,", len(districts), "districts")
        return cs.write()
