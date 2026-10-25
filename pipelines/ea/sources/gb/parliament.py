import html
import json
import re
import time
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb.os import BOROUGHS, COUNTIES, DIRECTIONS, IRISH, RIDINGS
from ea.store import DATA, connect

SOURCE = "gb.parliament"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://api.parliament.uk/uk-general-elections"
INDEX = f"{BASE}/general-elections/10"
PUBLISHER = "UK Parliament"
TITLE = "UK General Elections Results 1832–2019"
MEMBERS_QUERY = """
SELECT ?mp ?mpLabel ?district ?districtLabel ?start ?end ?cause ?causeLabel ?elected ?birth ?birthPrecision WHERE {
  ?mp p:P39 ?st . ?st ps:P39 wd:Q41582560 .
  ?st pq:P768 ?district .
  OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end }
  OPTIONAL { ?st pq:P1534 ?cause } OPTIONAL { ?st pq:P2715 ?elected }
  OPTIONAL { ?mp p:P569/psv:P569 [ wikibase:timeValue ?birth ; wikibase:timePrecision ?birthPrecision ] }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""
MEMBERS = "https://query.wikidata.org/sparql?" + urlencode({"query": MEMBERS_QUERY, "format": "json"})
ELECTION = date(1868, 11, 17)
CLOSED = date(1868, 12, 7)
SEATS = 658
UNION = date(1801, 1, 1)
PARTITION = date(1922, 12, 6)
GENERAL_ELECTION = "Q2074525"
PETITION_SEATED = "Q56123417"
PETITION = "election petition"
NOT_PARTIES = ("Independent", "Speaker")
WALES = {
    "Anglesey",
    "Brecknockshire",
    "Cardiganshire",
    "Carmarthenshire",
    "Carnarvonshire",
    "Denbighshire",
    "Flintshire",
    "Glamorgan",
    "Merionethshire",
    "Monmouthshire",
    "Montgomeryshire",
    "Pembrokeshire",
    "Radnorshire",
}
SCOTLAND = {
    "Aberdeen",
    "Aberdeenshire",
    "Angus",
    "Argyllshire",
    "Ayrshire",
    "Banffshire",
    "Berwickshire",
    "Buteshire",
    "Caithness",
    "Clackmannanshire",
    "Dumfriesshire",
    "Dunbartonshire",
    "Dundee",
    "East Lothian",
    "Edinburgh",
    "Fife",
    "Glasgow",
    "Inverness-shire",
    "Kincardineshire",
    "Kirkcudbrightshire",
    "Lanarkshire",
    "Midlothian",
    "Nairn",
    "Orkney",
    "Peebleshire",
    "Perthshire",
    "Renfrewshire",
    "Ross and Cromarty",
    "Roxburghshire",
    "Stirlingshire",
    "Sutherland",
    "West Lothian",
    "Wigtownshire",
}
NATIONS = {
    "ENG": ("England", "Q21"),
    "WLS": ("Wales", "Q25"),
    "SCT": ("Scotland", "Q22"),
    "IRL": ("Ireland", None),
}
ENDED_VIA = {
    "dissolution of parliament": "dissolved",
    "death in office": "died",
    "election petition": "annulled",
    "resignation": "resigned",
    "succession to a peerage": "disqualified",
    "elevation to the House of Lords": "disqualified",
    "appointed as a judge": "disqualified",
    "official appointment": "disqualified",
    "disqualification": "disqualified",
}
PRECISION = {"11": "day", "10": "month", "9": "year"}
SHARE_ONLY = 4
ESTIMATED = 2
SOURCE_NOTE = (
    "Candidates, parties, votes, and turnout for each constituency. The pages give no electorate, number of seats, or "
    "winners. Seats and winners come from Wikidata. Turnout in seats with more than one member is an estimate."
)
ELECTION_NOTE = (
    "Polling ran constituency by constituency from November 17 to December 7, 1868. Voting was public, since the "
    "secret ballot did not arrive until 1872. The source gives no polling day for each constituency, so every contest "
    "is listed under the general election as a whole."
)
BLOCK = "Each voter could vote for as many candidates as there were seats. The candidates with the most votes won."
LIMITED = (
    "In a constituency returning three members, each voter could vote for at most two candidates. In the City of "
    "London, which returned four, each voter could vote for three. The candidates with the most votes won."
)
DOUBLE_RETURN = (
    "The top two candidates tied, and the returning officer returned both for a single seat (a double return). The "
    "declared outcome keeps both. The court-revised result keeps the member the House of Commons seated."
)
REVISED = (
    "Election petitions changed who sat for this constituency. This result lists the members who held the seats "
    "after the petitions were decided. It does not include recounted votes."
)


def key(text: str) -> str:
    return " ".join(sorted(re.sub(r"[^a-z ]", " ", text.lower().replace("&", " and ")).split()))


def fetch() -> None:
    index = raw.fetch(SOURCE, INDEX, suffix=".html").path.read_text(encoding="utf-8")
    raw.fetch(SOURCE, MEMBERS, suffix=".json")
    for number in re.findall(rf'href="{BASE}/elections/(\d+)"', index):
        try:
            raw.cached(SOURCE, f"{BASE}/elections/{number}")
        except LookupError:
            raw.fetch(SOURCE, f"{BASE}/elections/{number}", suffix=".html")
            time.sleep(1)


def parse() -> None:
    index = raw.cached(SOURCE, INDEX).path.read_text(encoding="utf-8")
    contests: list[tuple[str, str, str, str]] = []
    rows: dict[str, list[tuple[str, str, str | None, str | None, int | None]]] = {}
    for page, label in re.findall(rf'href="{BASE}/elections/(\d+)"[^>]*>\s*([^<]+?)\s*<', index):
        text = raw.cached(SOURCE, f"{BASE}/elections/{page}").path.read_text(encoding="utf-8")
        constituency = re.search(rf'href="{BASE}/constituencies/(\d+)"', text)
        turnout = re.search(r"<td>Turnout</td>\s*<td>\s*([^<]*?)\s*</td>", text)
        if constituency is None or turnout is None:
            raise ValueError(f"election page {page} has no constituency or turnout")
        contests.append((page, html.unescape(label), constituency[1], turnout[1]))
        rows[page] = [
            (
                name_id,
                html.unescape(full),
                party_id or None,
                html.unescape(party) or None,
                int(votes.replace(",", "")) if votes else None,
            )
            for name_id, full, party_id, party, votes in re.findall(
                rf'candidate-names/(\d+)">([^<]+)</a></td>\s*<td>(?:<a href="{BASE}/parties/(\d+)">([^<]+)</a>)?</td>'
                r'(?:\s*<td class="number">([\d,]*)</td>)?',
                text,
            )
        ]
    if len(contests) != 420 or len({slug(label) for _, label, _, _ in contests}) != len(contests):
        raise ValueError(f"expected 420 constituencies with distinct slugs, got {len(contests)}")

    best: dict[tuple[str, ...], dict[str, str]] = {}
    for binding in json.loads(raw.cached(SOURCE, MEMBERS).path.read_text(encoding="utf-8"))["results"]["bindings"]:
        member = {
            field: value["value"].rsplit("/", 1)[-1] if value["type"] == "uri" else value["value"]
            for field, value in binding.items()
        }
        identity = (member["mp"], member["district"], member.get("start", ""), member.get("elected", ""))
        if identity not in best or int(member.get("birthPrecision", 0)) > int(best[identity].get("birthPrecision", 0)):
            best[identity] = member
    by_district: dict[str, list[dict[str, str]]] = defaultdict(list)
    for member in best.values():
        if member.get("elected") in (GENERAL_ELECTION, PETITION_SEATED):
            by_district[member["district"]].append(member)

    surnames = {
        page: {token for _, full, *_ in rows[page] for token in key(full.split(",")[0]).split() if len(token) > 3}
        for page, *_ in contests
    }
    pairs = sorted(
        (
            (
                sum(bool(surnames[page] & set(key(member["mpLabel"]).split())) for member in members),
                SequenceMatcher(None, key(label), key(members[0]["districtLabel"])).ratio(),
                page,
                district,
            )
            for page, label, _, _ in contests
            for district, members in by_district.items()
        ),
        reverse=True,
    )
    district_of: dict[str, str] = {}
    for _, _, page, district in pairs:
        if page not in district_of and district not in district_of.values():
            district_of[page] = district
    if len(district_of) != len(contests):
        raise ValueError(
            f"no Wikidata district for {[label for page, label, *_ in contests if page not in district_of]}"
        )

    matched: dict[tuple[str, str], dict[str, str]] = {}
    problems: list[str] = []
    for page, label, _, _ in contests:
        ranked = [name_id for name_id, *_ in sorted(rows[page], key=lambda row: -(row[4] or 0))]
        for member in by_district[district_of[page]]:
            tokens = key(member["mpLabel"]).split()
            scored = sorted(
                (
                    max(
                        SequenceMatcher(None, token, other).ratio()
                        for token in key(full.split(",")[0]).split() or [""]
                        for other in tokens or [""]
                    ),
                    len(set(key(full.partition(",")[2]).split()) & set(tokens)),
                    -ranked.index(name_id),
                    name_id,
                )
                for name_id, full, *_ in rows[page]
                if (page, name_id) not in matched
            )
            if not scored or scored[-1][0] < 0.8:
                if member.get("elected") == GENERAL_ELECTION:
                    problems.append(f"{label}: {member['mpLabel']} matches no candidate")
                continue
            matched[(page, scored[-1][3])] = member
    if problems:
        raise ValueError("\n".join(problems))

    members_of_name: dict[str, set[str]] = defaultdict(set)
    for (_, name_id), member in matched.items():
        members_of_name[name_id].add(member["mp"])
    members = {member["mp"]: member for member in matched.values()}
    seats = 0
    contest_rows: list[tuple[object, ...]] = []
    candidate_rows: list[tuple[object, ...]] = []
    for page, label, constituency, turnout in contests:
        uncontested = "uncontested" in turnout
        returned = sorted(
            (votes or 0, matched[(page, name_id)].get("causeLabel") == PETITION)
            for name_id, *_, votes in rows[page]
            if matched.get((page, name_id), {}).get("elected") == GENERAL_ELECTION
        )
        double_return = (
            not uncontested
            and len(returned) > 1
            and returned[0][0] == returned[1][0]
            and returned[0][1] != returned[1][1]
        )
        magnitude = len(returned) - double_return
        seats += magnitude
        if uncontested and len(rows[page]) != len(returned):
            problems.append(f"{label}: uncontested with {len(rows[page])} candidates for {len(returned)} seats")
        if not uncontested and not double_return:
            top = {name_id for name_id, *_ in sorted(rows[page], key=lambda row: -(row[4] or 0))[:magnitude]}
            winners = {
                name_id
                for name_id, *_ in rows[page]
                if matched.get((page, name_id), {}).get("elected") == GENERAL_ELECTION
            }
            if top != winners:
                problems.append(f"{label}: top {sorted(top)} differ from returned {sorted(winners)}")
        contest_rows.append(
            (
                page,
                label,
                constituency,
                district_of[page],
                float(turnout[:-1]) / 100 if turnout.endswith("%") else None,
                uncontested,
                "universit" in label.lower(),
                magnitude,
                double_return,
            )
        )
        for name_id, full, party_id, party, votes in rows[page]:
            member = matched.get((page, name_id))
            elsewhere = members_of_name.get(name_id, set())
            qid = member["mp"] if member else next(iter(elsewhere)) if len(elsewhere) == 1 else None
            known = members.get(qid or "")
            candidate_rows.append(
                (
                    page,
                    name_id,
                    full,
                    party_id,
                    party,
                    votes,
                    qid,
                    None if qid else f"{constituency}:{name_id}" if elsewhere else name_id,
                    None if known is None or known.get("birth", "-").startswith("-") else known["birth"][:10],
                    None if known is None else PRECISION.get(known.get("birthPrecision", "")),
                    None if member is None else member.get("elected") == GENERAL_ELECTION,
                    None if member is None else member.get("elected") == PETITION_SEATED,
                    None if member is None else member.get("start", "")[:10] or None,
                    None if member is None else member.get("end", "")[:10] or None,
                    None if member is None else member.get("causeLabel"),
                )
            )
    if seats != SEATS:
        problems.append(f"{seats} seats returned, expected {SEATS}")
    if problems:
        raise ValueError("\n".join(problems))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        contest_rows,
        schema={
            "page": pl.String,
            "label": pl.String,
            "constituency": pl.String,
            "district": pl.String,
            "turnout": pl.Float64,
            "uncontested": pl.Boolean,
            "university": pl.Boolean,
            "magnitude": pl.Int64,
            "double_return": pl.Boolean,
        },
        orient="row",
    ).write_parquet(PARSED / "contests.parquet")
    pl.DataFrame(
        candidate_rows,
        schema={
            "page": pl.String,
            "name_id": pl.String,
            "full": pl.String,
            "party_id": pl.String,
            "party": pl.String,
            "votes": pl.Int64,
            "qid": pl.String,
            "person": pl.String,
            "birth": pl.String,
            "birth_precision": pl.String,
            "elected": pl.Boolean,
            "petition_seated": pl.Boolean,
            "start": pl.String,
            "end": pl.String,
            "cause": pl.String,
        },
        orient="row",
    ).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="1868 United Kingdom general election by constituency",
            polity="GB",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        parliament = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=parliament,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=BASE,
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes=SOURCE_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, INDEX), parliament)
        for page in contests["page"]:
            cs.artifact(raw.cached(SOURCE, f"{BASE}/elections/{page}"), parliament)
        wikidata = cs.source("Wikidata", "Members of the 20th Parliament of the United Kingdom")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Members of the 20th Parliament of the United Kingdom",
            url="https://www.wikidata.org/wiki/Q41582560",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, MEMBERS), wikidata)

        gb = cs.polity("GB")
        cs.add("polity", id=gb, kind="sovereign_state", iso_code="GB")
        name(gb, "United Kingdom")
        cs.add(
            "name",
            entity_id=gb,
            text="United Kingdom of Great Britain and Ireland",
            lang="en",
            kind="historical",
            valid_from=UNION,
            valid_to=date(1927, 4, 12),
            is_preferred=False,
        )
        cs.add("slug", path="gb", entity_id=gb)
        cs.add("external_id", entity_id=gb, scheme="wikidata", value="Q145")
        cs.add("scheme", code="gb.nation", polity_id=gb, kind="administrative", rank=0, noun="country")
        cs.add("scheme", code="gb.country", polity_id=gb, kind="administrative", rank=1, noun="nation")
        cs.add("scheme", code="gb.pcon", polity_id=gb, kind="electoral_district", rank=1, noun="constituency")
        nation = cs.area("gb.nation", "GBI")
        cs.add("area", id=nation, scheme_code="gb.nation")
        cs.add("area_code", area_id=nation, code="GBI", valid_from=UNION, valid_to=PARTITION)
        name(nation, "United Kingdom of Great Britain and Ireland")
        cs.add("external_id", entity_id=nation, scheme="wikidata", value="Q174193")
        cs.add("polity_territory", polity_id=gb, area_id=nation, valid_from=UNION, valid_to=PARTITION)
        countries: dict[str, UUID] = {}
        for code, (label, item) in NATIONS.items():
            countries[code] = cs.area("gb.country", code)
            cs.add("area", id=countries[code], scheme_code="gb.country")
            cs.add("area_code", area_id=countries[code], code=code, valid_from=UNION, valid_to=PARTITION)
            cs.add("area_parent", area_id=countries[code], parent_id=nation, valid_from=UNION, valid_to=PARTITION)
            if countries[code] in cs.minted:
                name(countries[code], label)
            if item is not None:
                cs.add("external_id", entity_id=countries[code], scheme="wikidata", value=item)
        located = {borough: county for county, boroughs in BOROUGHS.items() for borough in boroughs}

        commons = cs.key("gb.body", "house-of-commons")
        cs.add("body", id=commons, polity_id=gb, kind="lower_house")
        name(commons, "House of Commons")
        cs.add("external_id", entity_id=commons, scheme="wikidata", value="Q11010")
        systems: dict[str, UUID] = {}
        for system, label, note in (
            ("fptp", "First past the post", None),
            ("block", "Block vote", BLOCK),
            ("limited", "Limited vote (Representation of the People Act 1867)", LIMITED),
        ):
            systems[system] = cs.key("gb.electoral_system", system)
            cs.add("electoral_system", id=systems[system], polity_id=gb, rules={"formula": system}, rules_version=1)
            name(systems[system], label)
            if note is not None:
                cs.add("note", entity_id=systems[system], kind="methodology", body_md=note)
        election = cs.key("gb.election", str(ELECTION))
        cs.add(
            "election",
            id=election,
            polity_id=gb,
            kind="general",
            date_from=ELECTION,
            date_to=CLOSED,
            status="held",
        )
        name(election, "1868 United Kingdom general election")
        cs.add("slug", path=f"gb/{ELECTION}-general", entity_id=election)
        cs.add("external_id", entity_id=election, scheme="wikidata", value=GENERAL_ELECTION)
        cs.add("note", entity_id=election, kind="context", body_md=ELECTION_NOTE)

        parties: dict[str, UUID] = {}
        for party_id, label in (
            candidates.filter(~pl.col("party").is_in(NOT_PARTIES))
            .select("party_id", "party")
            .drop_nulls()
            .unique()
            .sort("party_id")
            .iter_rows()
        ):
            parties[party_id] = cs.key(f"{SOURCE}.party", party_id)
            descriptor = label.startswith("Independent ") and label not in (
                "Independent Labour Party",
                "Independent Irish",
            )
            cs.add("party", id=parties[party_id], kind="independent_group" if descriptor else "party", polity_id=gb)
            name(parties[party_id], label)
        persons: dict[str, UUID] = {}
        for qid, person, full, birth, precision in candidates.select(
            "qid", "person", "full", "birth", "birth_precision"
        ).iter_rows():
            identity = qid or person
            if identity in persons:
                continue
            persons[identity] = cs.key("wikidata", qid) if qid else cs.key(f"{SOURCE}.person", f"{ELECTION}:{person}")
            cs.add(
                "person",
                id=persons[identity],
                birth_date=None if birth is None else date.fromisoformat(birth),
                birth_precision=precision,
            )
            surname, _, forenames = full.partition(", ")
            name(persons[identity], f"{forenames} {surname}".strip())

        by_page = {page: frame for (page,), frame in candidates.group_by("page")}
        for (
            page,
            label,
            constituency,
            district,
            turnout,
            uncontested,
            university,
            magnitude,
            double_return,
        ) in contests.iter_rows():
            area = None
            if not university:
                area = cs.area("gb.pcon", constituency)
                cs.add("area", id=area, scheme_code="gb.pcon")
                cs.add("area_code", area_id=area, code=constituency, valid_from=None)
                prefix = next((word for word in DIRECTIONS if label.startswith(f"{word} ")), None)
                stripped = label.removeprefix(f"{prefix} ") if prefix else label
                county = located.get(label) or (
                    "Yorkshire" if label in RIDINGS else COUNTIES.get(stripped, (stripped,))[0]
                )
                country = (
                    "IRL" if county in IRISH else "WLS" if county in WALES else "SCT" if county in SCOTLAND else "ENG"
                )
                cs.add("area_parent", area_id=area, parent_id=countries[country], valid_from=None)
                name(area, label)
                cs.add("external_id", entity_id=area, scheme="wikidata", value=district)
            unit = cs.unit("gb.pcon", constituency)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=gb,
                scheme_code="gb.pcon",
                code=constituency,
                area_relation="unlocated" if university else "equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
            if university:
                name(unit, label)
                cs.add("external_id", entity_id=unit, scheme="wikidata", value=district)
            contest_key = f"{ELECTION}:{constituency}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=commons,
                district_area_id=area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["fptp" if magnitude == 1 else "block" if magnitude == 2 else "limited"],
                default_result_kind="tacit" if uncontested else "counted",
            )
            name(contest, f"1868 general election in {label}")
            cs.add("slug", path=f"gb/{ELECTION}-general/{slug(label)}", entity_id=contest)
            if double_return:
                cs.add("note", entity_id=contest, kind="context", body_md=DOUBLE_RETURN)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if uncontested else "counted",
                status="active",
                source_id=parliament,
                is_default=True,
                completeness=1,
            )
            rows = by_page[page]
            petitioned = bool(rows["petition_seated"].any() or (rows["cause"] == PETITION).any() or double_return)
            revised = None
            if petitioned:
                revised = cs.key(f"{SOURCE}.result_set", f"{contest_key}:court_revised")
                cs.add(
                    "result_set",
                    id=revised,
                    election_id=election,
                    contest_id=contest,
                    stage="court_revised",
                    kind="counted",
                    status="active",
                    source_id=wikidata,
                    is_default=False,
                    completeness=0,
                )
                cs.add("note", entity_id=revised, kind="context", body_md=REVISED)
            for row in rows.iter_rows(named=True):
                option_key = f"{contest_key}:{row['name_id']}"
                option = cs.key("gb.option", option_key)
                party = parties.get(row["party_id"] or "")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=row["full"],
                )
                candidacy = cs.key("gb.candidacy", option_key)
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=persons[row["qid"] or row["person"]],
                    option_id=option,
                    role="head",
                    ballot_name=row["full"],
                    member_of_party_id=party,
                    is_independent=row["party"] == "Independent",
                )
                cs.add(
                    "citation",
                    entity_id=candidacy,
                    source_id=parliament,
                    locator={"election": page, "candidate_name": row["name_id"]},
                )
                if result_set in cs.minted and row["votes"] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=row["votes"],
                    )
                if row["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if revised is not None and (row["elected"] or row["petition_seated"]) and row["cause"] != PETITION:
                    cs.add("outcome", result_set_id=revised, option_id=option, kind="elected")
                if row["elected"] or row["petition_seated"]:
                    tenure = cs.key(f"{SOURCE}.tenure", f"{contest_key}:{row['qid']}")
                    ended = ENDED_VIA.get(row["cause"] or "")
                    cs.add(
                        "tenure",
                        id=tenure,
                        person_id=persons[row["qid"]],
                        body_id=commons,
                        from_date=None if row["start"] is None else date.fromisoformat(row["start"]),
                        to_date=None if row["end"] is None else date.fromisoformat(row["end"]),
                        acquired_via="elected",
                        contest_id=contest,
                        ended_via=ended,
                    )
                    if ended == "disqualified":
                        cs.add("note", entity_id=tenure, kind="context", body_md=row["cause"].capitalize())
            if result_set in cs.minted and turnout is not None:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=14,
                    count_round=0,
                    flags=SHARE_ONLY | (ESTIMATED if magnitude > 1 else 0),
                    value=turnout,
                )
        return cs.write()
