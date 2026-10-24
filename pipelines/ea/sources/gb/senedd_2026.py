import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import umit
from ea.sources.gb.senedd import SENC_2026, fold
from ea.store import connect

SOURCE = "gb.senedd_2026"
VERSION = "1"
URL = "https://research.senedd.wales/media/v4njg01m/senedd-election-2026.pdf"
DAY = date(2026, 5, 7)
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
PARTIES = {
    "Plaid Cymru": ("Plaid Cymru", "plaid-cymru"),
    "Reform UK": ("Reform UK", "reform-uk"),
    "Welsh Labour": ("Welsh Labour", "labour"),
    "Welsh Conservatives": ("Welsh Conservatives", "conservative"),
    "Wales Green Party": ("Wales Green Party", "green-party"),
    "Welsh Liberal Democrats": ("Welsh Liberal Democrats", "liberal-democrats"),
    "Heritage Party": ("Heritage Party", "heritage-party"),
    "Propel": ("Propel", "propel"),
    "Gwlad": ("Gwlad", "gwlad"),
    "Open Party": ("Open Party", "open-party"),
    "Welsh Christian Party": ("Welsh Christian Party", "welsh-christian-party"),
    "Britain's Communist Party": ("Britain's Communist Party", "britain-s-communist-party"),
    "Socialist Labour Party": ("Socialist Labour Party", "socialist-labour-party"),
    "Social Democratic Party": ("Social Democratic Party", "social-democratic-party"),
    "Official Monster Raving Loony Party": ("Official Monster Raving Loony Party", "official-monster-raving-loony-party"),
    "Welsh Trade Unionist and Socialist Coalition": (
        "Welsh Trade Unionist and Socialist Coalition",
        "welsh-trade-unionist-and-socialist-coalition",
    ),
}
WRAPPED = {
    "Official Monster Raving": "Official Monster Raving Loony Party",
    "Welsh Trade Unionist and": "Welsh Trade Unionist and Socialist Coalition",
}
SYSTEM = (
    "Closed list proportional representation: 96 members from 16 constituencies of six, each formed by pairing two "
    "UK Parliament constituencies. Each voter has one vote for a party's ranked list of up to eight candidates or for "
    "an individual candidate. A constituency's six seats go to parties and individual candidates by the D'Hondt "
    "method, and a party's seats go to its candidates in list order."
)
CAVEAT = (
    "Votes, electorates, and rejected ballots are from the Senedd Research briefing on the 2026 election, compiled from "
    "the constituency returning officers' returns and the Electoral Management Board. Each constituency's party columns "
    "were checked against its valid votes, and its seats against six. The six members elected are from the briefing's list of elected members; candidates who were not elected are not loaded. Individual candidates carry an independent head candidacy."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> list[dict[str, object]]:
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    by_name = {fold(label): label for label in SENC_2026}
    found: dict[str, dict[str, object]] = {}
    current: dict[str, object] | None = None
    electing: dict[str, object] | None = None
    heading: str | None = None
    for line in lines:
        text = line.strip()
        if fold(text) in by_name and len(text) < 40:
            heading = by_name[fold(text)]
            continue
        figure = re.match(r"^(Electorate|Valid votes cast|Total rejected ballot papers):\s+([\d,]+)$", text)
        if figure is not None and heading is not None:
            if figure.group(1) == "Electorate":
                current = found[heading] = {"label": heading, "rows": []}
            if current is not None:
                current[figure.group(1)] = int(figure.group(2).replace(",", ""))
            continue
        if text.startswith("Elected Members"):
            if current is not None:
                current["members"] = []
                electing = current
            current = None
            continue
        seated = re.match(r"^([1-6])\s{2,}(.+?)\s{2,}(.+)$", text)
        if seated is not None and electing is not None and len(electing["members"]) < 6:
            electing["members"].append(
                {
                    "position": int(seated.group(1)),
                    "name": seated.group(2).strip(),
                    "party": WRAPPED.get(seated.group(3).strip(), seated.group(3).strip()),
                }
            )
            continue
        row = re.match(r"^(.+?)\s{2,}([\d,]+)\s+[\d.]+%\s+(\d+)\s+(\d+|Individual)$", text)
        if row is not None and current is not None:
            current["rows"].append(
                {
                    "name": WRAPPED.get(row.group(1).strip(), row.group(1).strip()),
                    "votes": int(row.group(2).replace(",", "")),
                    "seats": int(row.group(3)),
                    "individual": WRAPPED.get(row.group(1).strip(), row.group(1).strip()) not in PARTIES,
                }
            )
    if set(found) != set(SENC_2026):
        raise ValueError(f"constituencies found: {sorted(set(SENC_2026) - set(found))} missing")
    for label, block in found.items():
        if sum(row["votes"] for row in block["rows"]) != block["Valid votes cast"]:
            raise ValueError(f"{label}: party columns do not sum to {block['Valid votes cast']}")
        if sum(row["seats"] for row in block["rows"]) != 6:
            raise ValueError(f"{label}: seats do not sum to 6")
    return [found[label] for label in SENC_2026]


def emit() -> Path:
    blocks = parse()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Senedd election 2026 constituency results by party, from the Senedd Research briefing",
            polity="GB-WLS",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        wales = cs.polity("GB-WLS")
        gb = cs.polity("GB")
        body = cs.key("gb.body", "senedd")
        if body in cs.minted:
            raise ValueError("publish gb.senedd first: the body must exist")
        system = cs.key("gb.electoral_system", "senedd-closed-list")
        if system in cs.minted:
            cs.add("electoral_system", id=system, polity_id=wales, rules={"formula": "dhondt"}, rules_version=1)
            name(system, "Closed list proportional representation (Senedd Cymru)")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)
        for seats, begin, end in ((60, date(1999, 5, 6), DAY), (96, DAY, None)):
            cs.add(
                "body_size",
                body_id=body,
                seats=seats,
                valid_from=begin,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="day",
            )
        known: dict[str, list[UUID]] = {}
        for person, text in conn.execute(
            """select distinct cy.person_id, n.text from candidacy cy
               join contest_option o on o.id = cy.option_id
               join contest c on c.id = o.contest_id
               join name n on n.entity_id = cy.person_id
               where c.body_id = %s""",
            [body],
        ).fetchall():
            known.setdefault(fold(text), []).append(person)
        source_id = cs.source("Senedd Research", "Senedd Election 2026: Research Briefing")
        cs.add(
            "source",
            id=source_id,
            kind="official_body",
            publisher="Senedd Research",
            title="Senedd Election 2026: Research Briefing",
            url="https://research.senedd.wales/",
            license="Senedd Commission Copyright, reproducible with acknowledgement",
            attribution="Source: Senedd Research, © Senedd Commission copyright.",
        )
        cs.artifact(raw.cached(SOURCE, URL), source_id)

        election = cs.key("gb.election", f"{DAY}:senedd")
        cs.add("election", id=election, polity_id=wales, kind="general", date_from=DAY, date_to=DAY, status="held")
        name(election, "2026 Senedd election")
        path = f"gb/wales/{DAY}-general"
        cs.add("slug", path=path, entity_id=election)

        parties: dict[str, UUID] = {}
        for block in blocks:
            label = block["label"]
            code = f"2026:{slug(label)}"
            area = cs.area("gb.senc", code, DAY)
            unit = cs.unit("gb.senc", code)
            if area in cs.minted or unit in cs.minted:
                raise ValueError("publish gb.senedd first: the 2026 constituencies must exist")
            contest_key = f"{DAY}:{slug(label)}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area,
                magnitude=6,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"2026 Senedd election in {label}")
            cs.add("slug", path=f"{path}/{slug(label)}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source_id,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEAT)
            for note, text in conn.execute(
                "select id, body_md from note where entity_id = %s and kind = 'caveat' and body_md <> %s",
                [result_set, CAVEAT],
            ).fetchall():
                cs.add("note_drop", note_id=note, entity_id=result_set, body_md=text, reason="replaced by the current caveat")
            for measure, amount in (
                (REGISTERED, block["Electorate"]),
                (VALID, block["Valid votes cast"]),
                (INVALID, block["Total rejected ballot papers"]),
            ):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=amount,
                )
            for row in block["rows"]:
                option = cs.key("gb.option", f"{contest_key}:{slug(row['name'])}")
                party = None
                if not row["individual"]:
                    title, key = PARTIES[row["name"]]
                    if key not in parties:
                        if key in umit.EXISTING:
                            parties[key] = cs.key(*umit.EXISTING[key])
                        else:
                            parties[key] = cs.key("gb.party", key)
                            if parties[key] in cs.minted:
                                cs.add("party", id=parties[key], kind="party", polity_id=gb)
                                name(parties[key], title)
                    party = parties[key]
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if row["individual"] else "list",
                    party_id=party,
                    ballot_label=row["name"],
                )
                if row["individual"]:
                    found = known.get(fold(row["name"]), [])
                    person = found[0] if len(found) == 1 else cs.key(f"{SOURCE}.person", f"{contest_key}:{slug(row['name'])}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, row["name"])
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.candidacy", f"{contest_key}:{slug(row['name'])}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        is_independent=True,
                    )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=row["votes"],
                )
                if row["seats"] > 0:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row["seats"])
            ranks: dict[str, int] = {}
            for number, member in enumerate(sorted(block["members"], key=lambda member: member["position"]), 1):
                seat = cs.key(f"{SOURCE}.seat", f"{contest_key}:{number}")
                cs.add(
                    "seat",
                    id=seat,
                    body_id=body,
                    district_area_id=area,
                    label=f"{label} seat {number}",
                    valid_from=DAY,
                    valid_from_precision="day",
                )
                ranks[member["party"]] = ranks.get(member["party"], 0) + 1
                option = cs.key("gb.option", f"{contest_key}:{slug(member['party'])}")
                found = known.get(fold(member["name"]), [])
                person = found[0] if len(found) == 1 else cs.key(f"{SOURCE}.person", f"{contest_key}:{slug(member['name'])}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, member["name"])
                member_key = f"{contest_key}:{slug(member['name'])}"
                candidacy = cs.key("gb.candidacy", member_key)
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    ballot_name=member["name"],
                    list_position=ranks[member["party"]],
                    is_independent=False,
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
                tenure = cs.key(f"{SOURCE}.tenure", member_key)
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    body_id=body,
                    seat_id=seat,
                    from_date=DAY,
                    from_precision="day",
                    acquired_via="elected",
                    contest_id=contest,
                )
                cs.add("tenure_affiliation", tenure_id=tenure, kind="party", party_id=parties[PARTIES[member["party"]][1]], valid_from=DAY)
        return cs.write()
