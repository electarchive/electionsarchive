import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.ie.dail import ALIASES, BALLOTS, EXHAUSTED, INVALID, PARTY_CODES, QUOTA, REGISTERED, VALID, VOTES, fold
from ea.sources.ie.dail_scans import DISTINCT, affiliation, full_name
from ea.store import DATA, connect

SOURCE = "ie.byelections"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
DAIL = date(2026, 5, 22)
SEANAD = date(2026, 11, 6)
RESULTS = "https://data.oireachtas.ie/ie/oireachtas/electoralProcess/electionResults"
DECLARATIONS = {
    "Dublin Central": f"{RESULTS}/dail/2026/2026-07-10_bye-election-to-the-34th-dail-which-was-held-on-22nd-may-2026-constituency-of-dublin-central_en.pdf",
    "Galway West": f"{RESULTS}/dail/2026/2026-07-10_bye-election-to-the-34th-dail-which-was-held-on-22nd-may-2026-constituency-of-galway-west_en.pdf",
}
CANDIDATES = f"{RESULTS}/seanad/2026/2026-10-16_seanad-bye-elections-2026-list-of-candidates_en.pdf"
VOLUME = f"{RESULTS}/dail/2002/2002-07-21_dail-general-election-may-2002-results-and-transfer-of-votes_en.pdf"
VOLUME_TITLE = "29th Dáil general election, May 2002: election results and transfer of votes, with the bye-elections to the 28th Dáil"
LABELS = {
    "Green Party - Comhaontas Glas": "Green Party",
    "People Before Profit / Solidarity": "People Before Profit–Solidarity",
}
LICENSE = "Oireachtas (Open Data) PSI Licence"
LICENSE_URL = "https://www.oireachtas.ie/en/open-data/license/"
SEANAD_CANDIDATES = (
    ("Ray McAdam", "FG"),
    ("Caitlín Ní Bhroin", None),
    ("Maeve Maria O'Rourke", None),
    ("Patrick Paul Walsh", None),
)
SEANAD_NOTE = (
    "Seán Kyne's seat on the Cultural and Educational Panel became vacant when he was elected to the Dáil for Galway "
    "West on 22 May 2026. The four candidates were nominated on the panel's nominating bodies sub-panel. The "
    "electorate is the members of the Dáil and the Seanad, who vote by post, and the poll closes at 11:00 on "
    "6 November 2026."
)
CAUSES = {
    "Dublin Central": "The by-election filled the seat Paschal Donohoe vacated when he resigned from the Dáil.",
    "Galway West": "The by-election filled the seat Catherine Connolly vacated when she was elected President.",
}
VACATED = {"Dublin Central": "Paschal Donohoe", "Galway West": "Catherine Connolly"}


def fetch() -> None:
    for url in [*DECLARATIONS.values(), CANDIDATES, VOLUME]:
        raw.fetch(SOURCE, url, suffix=".pdf")


NUMBER = re.compile(r"\d[\d,]*|—")
DELTA = re.compile(r"[+−-]\d[\d,]*")


def counts_of(text: str) -> tuple[str, list[int]]:
    tokens = text.split()
    values: list[int] = []
    while tokens and NUMBER.fullmatch(tokens[-1]):
        token = tokens.pop()
        values.append(0 if token == "—" else int(token.replace(",", "")))
    return " ".join(tokens), values[::-1]


def table_of(block: list[str]) -> dict:
    head = "\n".join(" ".join(line.split()) for line in block[:14])

    def number(pattern: str) -> int:
        found = re.search(pattern, head)
        if found is None:
            raise ValueError(pattern)
        return int(found[1].replace(",", ""))

    when = re.search(r"POLLING DAY (\d+)\w* (\w+), (\d{4})", head)
    constituency = re.search(r"^([A-ZÁÉÍÓÚ][A-ZÁÉÍÓÚ -]+?) QUOTA", head, re.MULTILINE)
    elected = re.search(r"VALID POLL [\d,]+ (.+)$", head, re.MULTILINE)
    if when is None or constituency is None or elected is None:
        raise ValueError(head)
    rows: list[tuple[str, list[int]]] = []
    pending = ""
    total: list[int] = []
    started = False
    for line in block:
        line = " ".join(line.split())
        if line.startswith("NAMES OF CANDIDATES"):
            started = True
            continue
        if not started or not line or line.startswith("†"):
            continue
        parts = line.split()
        if all(DELTA.fullmatch(part) or part == "—" for part in parts):
            continue
        while parts and DELTA.fullmatch(parts[-1]):
            parts.pop()
        label, values = counts_of(" ".join(parts))
        if not values:
            pending = f"{pending} {label}".strip()
            continue
        label = f"{pending} {label}".strip()
        pending = ""
        if not label:
            continue
        if label.startswith("TOTAL"):
            total = values
        else:
            rows.append((label, values))
    foot = next(line for line in block if line.strip().startswith("†")).strip().lstrip("†").strip()
    return {
        "day": datetime.strptime(" ".join(when.groups()), "%d %B %Y").date().isoformat(),
        "constituency": constituency[1].title(),
        "electorate": number(r"TOTAL ELECTORATE ([\d,]+)"),
        "valid": number(r"VALID POLL ([\d,]+)"),
        "quota": number(r"QUOTA ([\d,]+)"),
        "seats": number(r"NUMBER OF SEATS (\d+)"),
        "elected": elected[1].strip(),
        "vacancy": foot,
        "rows": rows,
        "total": total,
    }


def parse28() -> list[dict]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, VOLUME).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if "BYE-ELECTION," in line and "POLLING DAY" in line]
    races = []
    for start in starts:
        end = next(i for i in range(start + 8, len(lines)) if lines[i].strip().startswith("†"))
        race = table_of(lines[start : end + 1])
        pages = [int(m[1]) for line in lines[:start] if (m := re.match(r"\s*Page (\d+) CYAN", line))]
        race["page"] = pages[-1]
        races.append(race)
    for race in races:
        size = len(race["total"])
        where = f"{race['constituency']} {race['day']}"
        if any(len(values) != size for _, values in race["rows"]) or set(race["total"]) != {race["valid"]}:
            raise ValueError(f"{where}: rows do not match the total")
        for index in range(size):
            if sum(values[index] for _, values in race["rows"]) != race["valid"]:
                raise ValueError(f"{where}: count {index + 1} does not sum to the valid poll")
    return races


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    facts_path = DATA / "tmp" / "nh-ie" / "facts.json"
    if facts_path.exists():
        facts = json.loads(facts_path.read_text())
        (PARSED / "dail.json").write_text(json.dumps(facts["dail"], ensure_ascii=False, indent=1))
    races = parse28()
    (PARSED / "dail28.json").write_text(json.dumps(races, ensure_ascii=False, indent=1))
    print(len(races), "28th Dáil by-election tables")


def emit() -> Path:
    dail = json.loads((PARSED / "dail.json").read_text())
    roster = {
        (row[0], row[1]): row[2]
        for row in pl.read_parquet(DATA / "parsed" / "ie.dail" / "roster.parquet")
        .filter(pl.col("year") == 2024)
        .select("constituency", "name", "person")
        .iter_rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Dáil by-elections: the six held from 1998 to 2001 and the 2026 contests in Dublin Central and Galway West, each with every count, and the Seanad Cultural and Educational Panel by-election",
            polity="IE",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        ireland = cs.polity("IE")
        stv = cs.key("ie.electoral_system", "dail-eireann")
        dail_body = cs.key("ie.body", "dail-eireann")
        if {stv, dail_body} & cs.minted:
            raise ValueError("run ie.dail first")
        sources: dict[str, UUID] = {}
        for label, url in DECLARATIONS.items():
            title = f"Bye-election to the 34th Dáil held on 22nd May 2026, constituency of {label}"
            sources[label] = cs.source("Houses of the Oireachtas", title)
            cs.add(
                "source",
                id=sources[label],
                kind="official_body",
                publisher="Houses of the Oireachtas",
                title=title,
                url=url,
                license=LICENSE,
                license_url=LICENSE_URL,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[label])
        election = cs.key(f"{SOURCE}.election", f"{DAIL}:dail")
        cs.add(
            "election", id=election, polity_id=ireland, kind="by_election", date_from=DAIL, date_to=DAIL, status="held"
        )
        name(election, "2026 Dáil by-elections")
        cs.add("slug", path=f"ie/{DAIL}-by-election", entity_id=election)
        for race in dail:
            label = race["constituency"]
            code = f"2023:{slug(label)}"
            area, unit = cs.area("ie.dail", code), cs.unit("ie.dail", code)
            if {area, unit} & cs.minted:
                raise ValueError(f"{code} is not on record")
            contest = cs.key("ie.contest", f"{DAIL}:dail-eireann:{slug(label)}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=dail_body,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=stv,
                default_result_kind="counted",
            )
            name(contest, f"2026 {label} by-election")
            cs.add("slug", path=f"ie/{DAIL}-by-election/{slug(label)}", entity_id=contest)
            cs.add("note", entity_id=contest, kind="context", body_md=CAUSES[label])
            general = conn.execute(
                "select entity_id from slug where path = %s and is_current",
                [f"ie/2024-11-29-general/{slug(label)}"],
            ).fetchone()
            if general is None:
                raise ValueError(f"no 2024 contest for {label}")
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"{DAIL}:{slug(label)}"),
                person_id=cs.key("ie.dail.person", roster[label, fold(VACATED[label])]),
                body_id=dail_body,
                from_date=date(2024, 11, 29),
                to_date=date(2025, 1, 1),
                to_precision="year",
                acquired_via="elected",
                contest_id=general[0],
            )
            result_set = cs.key(f"{SOURCE}.result_set", f"{DAIL}:{slug(label)}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=sources[label],
                is_default=True,
                completeness=1,
            )
            if race["poll"] - race["spoiled"] != race["valid"]:
                raise ValueError(f"{label}: poll less spoiled is not the valid poll")
            values = []
            for measure, amount in (
                (REGISTERED, race["electorate"]),
                (BALLOTS, race["poll"]),
                (VALID, race["valid"]),
                (INVALID, race["spoiled"]),
                (QUOTA, race["quota"]),
            ):
                values.append((None, measure, 0, amount))
            counts = race["counts"]
            if sum(counts[0]["values"].values()) != race["valid"]:
                raise ValueError(f"{label}: first preferences do not sum to the valid poll")
            for count in counts[1:]:
                exhausted = race["non_transferable_by_count"][str(count["count"])]
                if sum(count["values"].values()) + exhausted != race["valid"]:
                    raise ValueError(f"{label}: count {count['count']} does not tie")
                values.append((None, EXHAUSTED, count["count"], exhausted))
            winner = race["elected"].split(" (")[0]
            for position, candidate in enumerate(race["candidates"], 1):
                party_label = LABELS.get(candidate["party"], candidate["party"])
                party = cs.key("ie.party", PARTY_CODES[party_label]) if party_label in PARTY_CODES else None
                if candidate["party"] != "Non-Party" and party is None:
                    raise ValueError(f"no party code for {candidate['party']}")
                key = f"{DAIL}:dail-eireann:{slug(label)}:{fold(candidate['name'])}"
                earlier = roster.get((label, fold(candidate["name"])))
                person = cs.key("ie.dail.person", earlier) if earlier else cs.key(f"{SOURCE}.person", key)
                if person in cs.minted and earlier is None:
                    cs.add("person", id=person)
                    name(person, candidate["name"])
                option = cs.key("ie.option", key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=position,
                    ballot_label=candidate["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("ie.candidacy", key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate["name"],
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                last = 0
                for count in counts:
                    if candidate["name"] in count["values"]:
                        last = count["count"]
                        values.append((option, VOTES, count["count"], count["values"][candidate["name"]]))
                kind = "elected" if candidate["name"] == winner else "lost" if last == len(counts) else "eliminated"
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            for option, measure, count_round, amount in values:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=count_round,
                    flags=0,
                    value=amount,
                )

        earlier = json.loads((PARSED / "dail28.json").read_text())
        roster_2002 = {
            (row[0], row[1]): row[2]
            for row in pl.read_parquet(DATA / "parsed" / "ie.dail" / "roster.parquet")
            .filter(pl.col("year") == 2002)
            .select("constituency", "name", "person")
            .iter_rows()
        }
        known: dict[str, list[UUID]] = {}
        for text, found in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'IE'"
        ).fetchall():
            known.setdefault(fold(text), []).append(found)
        strays = {
            str(value): entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'ie.dail.party'"
            ).fetchall()
        }
        parties: dict[str, UUID] = {}

        def party_id(label: str) -> UUID:
            if label not in parties:
                canonical = ALIASES.get(label, label)
                if label in PARTY_CODES:
                    parties[label] = cs.key("ie.party", PARTY_CODES[label])
                elif label not in DISTINCT and slug(canonical) in strays:
                    parties[label] = strays[slug(canonical)]
                elif label not in DISTINCT and len(set(known.get(fold(canonical), ()))) == 1:
                    parties[label] = known[fold(canonical)][0]
                else:
                    parties[label] = cs.key("ie.dail.party", slug(canonical))
                    if parties[label] in cs.minted:
                        cs.add("party", id=parties[label], kind="party", polity_id=ireland)
                        name(parties[label], DISTINCT.get(label, label))
            return parties[label]

        volume = cs.source("Houses of the Oireachtas", VOLUME_TITLE)
        cs.add(
            "source",
            id=volume,
            kind="official_body",
            publisher="Houses of the Oireachtas",
            title=VOLUME_TITLE,
            url=VOLUME,
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        cs.artifact(raw.cached(SOURCE, VOLUME), volume)
        elections: dict[str, UUID] = {}
        for race in sorted(earlier, key=lambda race: (race["day"], race["constituency"])):
            day, label = date.fromisoformat(race["day"]), race["constituency"]
            if race["day"] not in elections:
                election = elections[race["day"]] = cs.key(f"{SOURCE}.election", f"{day}:dail")
                cs.add(
                    "election",
                    id=election,
                    polity_id=ireland,
                    kind="by_election",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                together = sum(other["day"] == race["day"] for other in earlier) > 1
                name(election, f"{day.year} Dáil by-election{'s' if together else ''}")
                cs.add("slug", path=f"ie/{day}-by-election", entity_id=election)
            election = elections[race["day"]]
            code = f"1995:{slug(label)}"
            area, unit = cs.area("ie.dail", code), cs.unit("ie.dail", code)
            if {area, unit} & cs.minted:
                raise ValueError(f"{code} is not on record")
            contest = cs.key("ie.contest", f"{day}:dail-eireann:{slug(label)}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=dail_body,
                district_area_id=area,
                magnitude=race["seats"],
                round_number=1,
                electoral_system_id=stv,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} {label} by-election")
            cs.add("slug", path=f"ie/{day}-by-election/{slug(label)}", entity_id=contest)
            cs.add(
                "note",
                entity_id=contest,
                kind="context",
                body_md=f"{race['vacancy']}. Printed on page {race['page']} of the volume.",
            )
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{slug(label)}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=volume,
                is_default=True,
                completeness=1,
            )
            values = [
                (None, REGISTERED, 0, race["electorate"]),
                (None, VALID, 0, race["valid"]),
                (None, QUOTA, 0, race["quota"]),
            ]
            candidates, spoilt = [], []
            for text, counts in race["rows"]:
                (spoilt if text.startswith("Non-transfer") else candidates).append((text.replace("’", "'"), counts))
            if len(spoilt) != 1:
                raise ValueError(f"{label}: no non-transferable row")
            values.extend((None, EXHAUSTED, count, amount) for count, amount in enumerate(spoilt[0][1], 1) if amount)
            winner = fold(re.sub(r"\s*\([^()]*\)\s*$", "", race["elected"]).replace("’", "'"))
            size = len(spoilt[0][1])
            chosen = 0
            for position, (text, counts) in enumerate(candidates, 1):
                person_name = full_name(text)
                party_label = affiliation(1998, text)[0]
                party = party_id(party_label) if party_label else None
                key = f"{day}:dail-eireann:{slug(label)}:{fold(person_name)}"
                earlier_person = roster_2002.get((label, fold(person_name)))
                person = cs.key("ie.dail.person", earlier_person) if earlier_person else cs.key(f"{SOURCE}.person", key)
                if person in cs.minted and earlier_person is None:
                    cs.add("person", id=person)
                    name(person, person_name)
                option = cs.key("ie.option", key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=position,
                    ballot_label=person_name,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("ie.candidacy", key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=person_name,
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                last = max((count for count, amount in enumerate(counts, 1) if amount), default=0)
                values.extend((option, VOTES, count, counts[count - 1]) for count in range(1, last + 1))
                elected = fold(person_name) == winner
                chosen += elected
                kind = "elected" if elected else "lost" if last == size else "eliminated"
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            if chosen != race["seats"]:
                raise ValueError(f"{label}: {chosen} candidates match the elected name")
            for option, measure, count_round, amount in values:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=count_round,
                    flags=0,
                    value=amount,
                )

        candidates = cs.source("Houses of the Oireachtas", "Seanad bye-elections 2026: list of candidates")
        cs.add(
            "source",
            id=candidates,
            kind="official_body",
            publisher="Houses of the Oireachtas",
            title="Seanad bye-elections 2026: list of candidates",
            url=CANDIDATES,
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        cs.artifact(raw.cached(SOURCE, CANDIDATES), candidates)
        seanad = cs.key("ie.body", "seanad-eireann")
        if seanad in cs.minted:
            cs.add("body", id=seanad, polity_id=ireland, kind="upper_house")
            name(seanad, "Seanad Éireann")
            cs.add("body_size", body_id=seanad, seats=60, valid_from=date(1938, 4, 27), valid_to=None)
        by_election = cs.key(f"{SOURCE}.election", f"{SEANAD}:seanad")
        cs.add(
            "election",
            id=by_election,
            polity_id=ireland,
            kind="by_election",
            date_from=SEANAD,
            date_to=SEANAD,
            status="scheduled",
        )
        name(by_election, "2026 Seanad by-election")
        cs.add("slug", path=f"ie/{SEANAD}-by-election", entity_id=by_election)
        contest = cs.key("ie.contest", f"{SEANAD}:seanad-eireann:cultural-and-educational")
        cs.add(
            "contest",
            id=contest,
            election_id=by_election,
            kind="body_seats",
            body_id=seanad,
            magnitude=1,
            round_number=1,
            electoral_system_id=stv,
            default_result_kind="counted",
        )
        name(contest, "Cultural and Educational Panel by-election")
        cs.add("slug", path=f"ie/{SEANAD}-by-election/cultural-and-educational-panel", entity_id=contest)
        cs.add("note", entity_id=contest, kind="context", body_md=SEANAD_NOTE)
        for position, (candidate, code) in enumerate(SEANAD_CANDIDATES, 1):
            key = f"{SEANAD}:seanad-eireann:{fold(candidate)}"
            person = cs.key(f"{SOURCE}.person", key)
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, candidate)
            party = cs.key("ie.party", code) if code else None
            option = cs.key("ie.option", key)
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=candidate,
            )
            cs.add(
                "candidacy",
                id=cs.key("ie.candidacy", key),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate,
                member_of_party_id=party,
            )
        return cs.write()
