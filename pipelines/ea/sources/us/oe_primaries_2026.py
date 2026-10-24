import csv
import re
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.oe_primaries_2026"
VERSION = "1"
BASE = "https://raw.githubusercontent.com/openelections/openelections-data-{}/master/2026/{}"
FILES = {
    "TX": ("tx", "2026-03-03", ["20260303__tx__primary__county.csv"]),
    "CA": ("ca", "2026-06-02", ["20260602__ca__primary__county.csv"]),
    "MI": ("mi", "2026-08-04", ["20260804__mi__primary__county.csv"]),
    "PA": ("pa", "2026-05-19", ["20260519__pa__primary__county.csv"]),
    "WV": ("wv", "2026-05-13", ["20260513__wv__primary__county.csv"]),
    "MS": ("ms", "2026-03-10", ["20260310__ms__democratic__primary__county.csv", "20260310__ms__republican__primary__county.csv"]),
    "SD": ("sd", "2026-06-02", ["20260602__sd__primary__county.csv"]),
    "WY": ("wy", "2026-08-18", ["20260818__wy__primary__county.csv"]),
    "OR": ("or", "2026-05-19", ["20260519__or__primary__county.csv"]),
    "KY": ("ky", "2026-05-19", ["20260519__ky__primary__county.csv"]),
    "CO": ("co", "2026-06-30", ["20260630__co__primary__county.csv"]),
    "AZ": ("az", "2026-07-21", ["20260721__az__primary__county.csv"]),
    "TN": ("tn", "2026-08-01", ["20260801__tn__primary__county.csv"]),
}
FIPS = {"TX": "48", "CA": "06", "MI": "26", "PA": "42", "WV": "54", "MS": "28", "SD": "46", "WY": "56", "OR": "41", "KY": "21", "CO": "08", "AZ": "04", "TN": "47"}
RUNOFF_SYSTEMS = {"TX": "two_round", "MS": "two_round", "SD": "two_round_35"}
THRESHOLDS = {"TX": 50, "MS": 50, "SD": 35}
OFFICES = {"U.S. House": "house", "U.S. Senate": "senate", "Governor": "governor"}
TOP_TWO = {"CA"}
AT_LARGE = {"SD", "WY"}
REDISTRICTED = {"TX", "CA"}
PARTIES = {
    "REP": ("republican", "republican-party"),
    "R": ("republican", "republican-party"),
    "Republican": ("republican", "republican-party"),
    "DEM": ("democratic", "democratic-party"),
    "D": ("democratic", "democratic-party"),
    "Democratic": ("democratic", "democratic-party"),
    "LIB": ("libertarian", "libertarian-party"),
    "LBT": ("libertarian", "libertarian-party"),
    "Libertarian": ("libertarian", "libertarian-party"),
    "GRN": ("green", "green-party"),
    "Green": ("green", "green-party"),
    "Peace and Freedom": ("peace-and-freedom", "CA:peace-and-freedom-party"),
    "American Independent": ("american-independent", "american-independent-party"),
    "No Party Preference": ("", "CA:no-party-preference"),
    "NOL": ("no-labels", "no-labels-party"),
}
ALIASES = {("MS", "jeffdavis"): "jeffersondavis"}
POOLED = re.compile(r"write-?ins?\b|^scattering$", re.I)
JUNK = re.compile(r"^(under|over) votes$|^\* withdrawn candidate$|^total|blank|registered voters", re.I)
ORDINALS = {1: "st", 2: "nd", 3: "rd"}
CAVEAT = "Held under the 2026 congressional map, which the archive has not yet loaded as a boundary set, so this contest is not yet tied to a district shape."
METHOD = "Results by county from OpenElections' 2026 primary file for {state}, a community compilation of the state's county results. Candidates who received votes are listed; write-in votes are pooled."


def title(text: str) -> str:
    if text != text.upper():
        return text
    words = []
    for word in text.split():
        if re.fullmatch(r"(II|III|IV|VI)", word):
            words.append(word)
        elif re.match(r"MC[A-Z]{2}", word):
            words.append("Mc" + word[2:].capitalize())
        else:
            words.append(re.sub(r"[A-Za-z]+", lambda match: match[0].capitalize(), word))
    return " ".join(words)


def fetch() -> None:
    for code, _, names in FILES.values():
        for name in names:
            raw.fetch(SOURCE, BASE.format(code, name), suffix=".csv")


def parse() -> None:
    pass


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 U.S. House, U.S. Senate, and governor primaries in Texas, California, and Michigan, by county, from OpenElections",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state_names = dict(
            conn.execute(
                """select right(p.iso_code, 2), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code ~ '^US-[A-Z]{2}$'"""
            ).fetchall()
        )
        templates = {
            value: row
            for value, *row in conn.execute(
                """select x.value, co.kind, co.office_id, co.body_id, co.district_area_id, co.seat_id,
                          co.boundary_set_id, co.magnitude, co.term, co.default_result_kind
                   from contest co join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                   where x.value ~ '^(2026-11-03|2024-11-05):[A-Z]{2}:house:[0-9]+$'
                      or x.value ~ '^(2026-11-03|2020-11-03):[A-Z]{2}:senate$'
                      or x.value ~ '^(2026-11-03|2022-11-08):[A-Z]{2}:governor$'"""
            ).fetchall()
        }
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "two_round", "two_round_35", "top_two")}
        counties = {
            (code[:2], re.sub(r"[^a-z]", "", text.lower())): id
            for code, text, id in conn.execute(
                """select u.code, n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code ~ %s""",
                [f"^({'|'.join(FIPS.values())})"],
            ).fetchall()
        }
        pool: dict[tuple[str, str], set[UUID]] = defaultdict(set)
        for state, person, text in conn.execute(
            """select split_part(x.value, ':', 2), cy.person_id, n.text
               from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ ('^20(1[4-9]|2[0-9])-[0-9-]+:(' || %s || '):(house|senate|governor)')""",
            ['|'.join(FILES)],
        ).fetchall():
            pool[(state, slug(text))].add(person)
        persons: dict[tuple[str, str], UUID] = {}
        parties: dict[str, UUID] = {}

        def party_of(label: str | None) -> UUID | None:
            if label not in PARTIES:
                return None
            key = PARTIES[label][1]
            if key not in parties:
                parties[key] = cs.key("us.party", key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind="party", polity_id=cs.polity("US"))
                    name(parties[key], {"no-labels-party": "No Labels Party"}[key])
            return parties[key]

        def person_of(state: str, text: str) -> UUID:
            if (state, slug(text)) not in persons:
                found = pool.get((state, slug(text)), set())
                if len(found) == 1:
                    persons[(state, slug(text))] = next(iter(found))
                else:
                    id = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                    if id in cs.minted:
                        cs.add("person", id=id)
                        name(id, text)
                    persons[(state, slug(text))] = id
            return persons[(state, slug(text))]

        sources: dict[str, UUID] = {}
        retired_persons: set[UUID] = set()
        elections: dict[str, UUID] = {}
        totals: dict[str, int] = {}
        for state, (code, day, files) in FILES.items():
            state_name = state_names[state]
            publisher, source_title = "OpenElections", f"{state_name} 2026 primary results by county"
            sources[state] = cs.source(publisher, source_title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="community",
                    publisher=publisher,
                    title=source_title,
                    url=f"https://github.com/openelections/openelections-data-{code}",
                    license=None,
                )
            for file in files:
                cs.artifact(raw.cached(SOURCE, BASE.format(code, file)), sources[state])
            election_key = f"{day}:{state}:primary"
            elections[state] = cs.key("us.election", election_key)
            if elections[state] in cs.minted:
                cs.add(
                    "election",
                    id=elections[state],
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=None,
                    kind="primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(elections[state], f"2026 {state_name} primary election")
                cs.add("slug", path=f"us/{state.lower()}/{day}-primary", entity_id=elections[state])
            grouped: dict[tuple[str, str, str], dict[tuple[str, str, str], dict[str, int]]] = defaultdict(
                lambda: defaultdict(lambda: defaultdict(int))
            )
            scope_labels: dict[str, str] = {}
            junk: dict[tuple[str, str, str], set[str]] = defaultdict(set)
            for file in files:
                with raw.cached(SOURCE, BASE.format(code, file)).path.open(newline="") as file_handle:
                    for row in csv.DictReader(file_handle):
                        chamber = OFFICES.get(row["office"])
                        if chamber is None or not row["votes"].isdigit() or row["party"] not in PARTIES:
                            continue
                        scope = PARTIES[row["party"]][0]
                        if scope == "" and state not in TOP_TWO:
                            continue
                        scope_labels.setdefault(scope, row["party"])
                        district = ("0" if state in AT_LARGE else row["district"]) if chamber == "house" else ""
                        if JUNK.search(row["candidate"].strip()):
                            junk[(chamber, district, "" if state in TOP_TWO else scope)].add(title(row["candidate"].strip()))
                            continue
                        pooled = POOLED.search(row["candidate"]) is not None or row.get("write_in") == "Y"
                        label = row["party"] if not pooled else ""
                        candidate = "Write-ins" if pooled else title(row["candidate"].strip())
                        county = re.sub(r"[^a-z]", "", row["county"].lower())
                        county = ALIASES.get((state, county), county)
                        grouped[(chamber, district, "" if state in TOP_TWO else scope)][
                            (candidate, label, "pooled" if pooled else "")
                        ][county] += int(row["votes"])
            for (chamber, district, scope), labels in sorted(junk.items()):
                key_tail = chamber + (f":{district}" if district else "") + (f":{scope}" if scope else "")
                for label in sorted(labels):
                    option = cs.key("us.option", f"{day}:{state}:{key_tail}:{slug(label)}")
                    if option not in cs.minted:
                        reason = f"{label} is a report line, not a candidate"
                        cs.add(
                            "entity_retirement",
                            entity_id=cs.key("us.candidacy", f"{day}:{state}:{key_tail}:{slug(label)}"),
                            reason=reason,
                        )
                        cs.add("entity_retirement", entity_id=option, reason=reason)
                        gone = cs.key(f"{SOURCE}.person", f"{state}:{slug(label)}")
                        if gone not in retired_persons and gone not in cs.minted:
                            retired_persons.add(gone)
                            cs.add("entity_retirement", entity_id=gone, reason=reason)
            for (chamber, district, scope), candidates in sorted(grouped.items()):
                key_tail = chamber + (f":{district}" if district else "") + (f":{scope}" if scope else "")
                contest_key = f"{day}:{state}:{key_tail}"
                contest = cs.key("us.contest", contest_key)
                template_key = {
                    "house": f"2026-11-03:{state}:house:{district}",
                    "senate": f"2026-11-03:{state}:senate",
                    "governor": f"2026-11-03:{state}:governor",
                }[chamber]
                fallback = {
                    "house": f"2024-11-05:{state}:house:{district}",
                    "senate": f"2020-11-03:{state}:senate",
                    "governor": f"2022-11-08:{state}:governor",
                }[chamber]
                shapeless = template_key not in templates
                kind, office, body, area, seat, plan, magnitude, term, result_kind = templates[
                    fallback if shapeless else template_key
                ]
                if shapeless and chamber == "house":
                    area, plan = None, None
                scope_label = scope.replace("-", " ").title()
                if chamber == "house":
                    number = int(district)
                    suffix = "th" if 10 <= number % 100 <= 20 else ORDINALS.get(number % 10, "th")
                    at_large = number == 0
                    title_text = f"2026 U.S. House {scope_label + ' ' if scope else ''}primary in {state_name}'s " + (
                        "at-large congressional district" if at_large else f"{number}{suffix} congressional district"
                    )
                elif chamber == "senate":
                    title_text = f"2026 U.S. Senate {scope_label + ' ' if scope else ''}primary in {state_name}"
                else:
                    title_text = f"2026 {state_name} gubernatorial " + (
                        f"{scope_label} primary" if scope else "top-two primary"
                    )
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[state],
                    kind=kind,
                    office_id=office,
                    body_id=body,
                    district_area_id=area,
                    seat_id=seat,
                    boundary_set_id=plan,
                    magnitude=magnitude,
                    party_scope_id=party_of(scope_labels[scope])
                    if scope
                    else None,
                    term=term,
                    round_number=1,
                    electoral_system_id=systems["top_two"] if state in TOP_TWO else systems[RUNOFF_SYSTEMS.get(state, "fptp")],
                    default_result_kind=result_kind,
                )
                name(contest, title_text)
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{day}-primary/{chamber}"
                    + (f"-{district}" if district else "")
                    + (f"-{scope}" if scope else ""),
                    entity_id=contest,
                )
                cs.add("note", entity_id=contest, kind="methodology", body_md=METHOD.format(state=state_name))
                if shapeless and chamber == "house" and state in REDISTRICTED:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=CAVEAT)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[state],
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[state],
                    is_default=True,
                    completeness=1,
                )
                named: list[tuple[int, UUID]] = []
                for (candidate, label, pooled), by_county in sorted(candidates.items()):
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    party_id = party_of(label)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if pooled else "candidate",
                        party_id=None if pooled else party_id if (state in TOP_TWO or scope) else None,
                        ballot_label=candidate,
                    )
                    if not pooled:
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}"),
                            person_id=person_of(state, candidate),
                            option_id=option,
                            role="head",
                            ballot_name=candidate,
                            is_independent=party_id is None,
                        )
                    for county, votes in sorted(by_county.items()):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=counties[(FIPS[state], county)],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=votes,
                        )
                    if not pooled:
                        named.append((sum(by_county.values()), option))
                named.sort(key=lambda entry: -entry[0])
                everyone = sum(votes for votes, _ in named)
                if state in TOP_TWO or (len(named) > 1 and state in THRESHOLDS and named[0][0] * 100 <= THRESHOLDS[state] * everyone):
                    leaders = named[:2]
                    if len(named) > 2 and named[1][0] == named[2][0]:
                        cs.add("note", entity_id=result_set, kind="caveat", body_md="A tie for the last advancing place leaves the outcome unrecorded.")
                        continue
                    for _, option in leaders:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
                elif named:
                    if len(named) > 1 and named[0][0] == named[1][0]:
                        continue
                    cs.add("outcome", result_set_id=result_set, option_id=named[0][1], kind="won", seats=None)
                totals[state] = totals.get(state, 0) + 1
        print(totals)
        return cs.write()
