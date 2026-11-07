import gzip
import html
import re
import tarfile
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import yaml

from ea import raw
from ea.changeset import Changeset
from ea.sources.licenses import UNLICENSED
from ea.sources.us.legislature_size import LOWER, STATES
from ea.store import connect

SOURCE = "us.openstates"
VERSION = "1"
COMMIT = "bcdda400007b22d92cc889ca946d44a4c9811a28"
ARCHIVE = f"https://codeload.github.com/openstates/people/tar.gz/{COMMIT}"
LANDING = f"https://github.com/openstates/people/tree/{COMMIT}"
NCSL = "https://www.ncsl.org/about-state-legislatures/state-partisan-composition"
SENATE_MAJORITY = "https://alaskasenate.org/"
HOUSE_MAJORITY = "https://web.archive.org/web/20260625220223id_/https://alaskahousemajority.com/members/"
SESSION = date(2025, 1, 21)
CAUCUSES = {
    "AK:senate-majority": (
        "AK:senate",
        "Alaska Senate Majority",
        "Senate Majority",
        "Alaska Senate Majority",
        SENATE_MAJORITY,
        {"Stevens", "Bjorkman", "Claman", "Dunbar", "Giessel", "Gray-Jackson", "Hoffman"}
        | {"Kawasaki", "Kiehl", "Merrick", "Olson", "Stedman", "Tobin", "Wielechowski"},
    ),
    "AK:house-majority-coalition": (
        "AK:house",
        "Alaska House Majority Coalition",
        "House Majority Coalition",
        "House Majority Coalition",
        HOUSE_MAJORITY,
        {"Edgmon", "Kopp", "Stutes", "Foster", "Fields", "Story", "Josephson", "Galvin", "Schrage", "Dibert"}
        | {"Eischeid", "Gray", "Carrick", "Himschoot", "Mears", "Mina", "Hall", "Hannan", "Holland", "Jimmie"}
        | {"Frier"},
    ),
}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Democratic-Farmer-Labor": "MN:minnesota-democratic-farmer-labor-party",
    "Forward": "forward-party",
}
LABELS = {
    "house": "House",
    "senate": "Senate",
    "assembly": "Assembly",
    "general-assembly": "General Assembly",
    "house-of-delegates": "House of Delegates",
    "legislature": "Legislature",
}
ORDINALS = {
    word: index
    for index, word in enumerate(
        [
            "zeroth",
            "first",
            "second",
            "third",
            "fourth",
            "fifth",
            "sixth",
            "seventh",
            "eighth",
            "ninth",
            "tenth",
            "eleventh",
            "twelfth",
            "thirteenth",
            "fourteenth",
            "fifteenth",
            "sixteenth",
            "seventeenth",
            "eighteenth",
            "nineteenth",
            "twentieth",
        ]
    )
}
DRIFT = 3


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".tar.gz")
    raw.fetch(SOURCE, NCSL, suffix=".html")
    raw.fetch(SOURCE, SENATE_MAJORITY, suffix=".html")
    raw.fetch(SOURCE, HOUSE_MAJORITY, suffix=".html")


def fold(text: str) -> str:
    return re.sub(
        r"[^a-z]+", " ", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    ).strip()


def code(state: str, district: str) -> str:
    text = unicodedata.normalize("NFKD", district).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z]+|\d+", text)
    number = None
    if words and words[0] in ORDINALS:
        number = str(ORDINALS[words.pop(0)])
    elif re.match(r"^\d+(st|nd|rd|th) ", text):
        number = words.pop(0)
        words.pop(0)
    words = [word for word in words if word != "and"]
    if number is not None:
        words.append(number)
    parts: list[str] = []
    for word in words:
        if parts and word.isalpha() and parts[-1].isalpha():
            parts[-1] += word
        else:
            parts.append(word)
    parts = [str(int(part)) if part.isdigit() else part for part in parts]
    if state == "ID" and len(parts) == 2 and len(parts[1]) == 1:
        parts = parts[:1]
    return f"{state}:{'-'.join(parts)}"


def text(path: Path) -> str:
    content = path.read_bytes()
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return html.unescape(
        re.sub(
            r"\s+",
            " ",
            re.sub(
                r"<[^>]+>",
                " ",
                re.sub(r"<(script|style).*?</\1>", "", content.decode("utf-8", "ignore"), flags=re.DOTALL),
            ),
        )
    )


def members() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    with tarfile.open(raw.cached(SOURCE, ARCHIVE).path) as archive:
        for entry in archive:
            parts = entry.name.split("/")
            if not entry.isfile() or len(parts) != 5 or parts[2] in ("us", "dc", "pr") or parts[3] != "legislature":
                continue
            file = archive.extractfile(entry)
            if file is None:
                continue
            person = yaml.load(file.read(), Loader=yaml.CSafeLoader)
            for role in person.get("roles", []):
                if role.get("end_date") is not None or role["type"] not in ("upper", "lower", "legislature"):
                    continue
                if re.search(r"Tribe|Band|Nation", role["district"]):
                    continue
                state = parts[2].upper()
                chamber = (
                    "legislature"
                    if role["type"] == "legislature"
                    else "senate"
                    if role["type"] == "upper"
                    else LOWER.get(state, "house")
                )
                parties = [party["name"] for party in person.get("party", []) if party.get("end_date") is None]
                if len(parties) != 1:
                    raise ValueError(f"{person['id']} has {len(parties)} current parties")
                found.append(
                    {
                        "id": person["id"],
                        "name": person["name"],
                        "family": person.get("family_name") or person["name"].split()[-1],
                        "given": person.get("given_name") or person["name"].split()[0],
                        "others": [other["name"] for other in person.get("other_names", [])],
                        "state": state,
                        "chamber": chamber,
                        "district": role["district"],
                        "code": code(state, role["district"]),
                        "start": date.fromisoformat(str(role["start_date"])) if role.get("start_date") else None,
                        "party": parties[0].split("/")[0],
                        "wikidata": person.get("ids", {}).get("wikidata"),
                    }
                )
    return found


def composition() -> dict[tuple[str, str], tuple[int, int, int, int, int]]:
    content = raw.cached(SOURCE, NCSL).path.read_text(encoding="utf-8", errors="ignore")
    table = content[content.index('id="partisanComp"') :]
    table = table[: table.index("</table>")]
    found: dict[tuple[str, str], tuple[int, int, int, int, int]] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        state = STATES.get(cells[0]) if cells else None
        if state is None:
            continue
        numbers = [int(cell) if cell.isdigit() else 0 for cell in cells[1:11]]
        if state == "NE":
            found[(state, "legislature")] = (numbers[5], 0, 0, 0, numbers[9])
            continue
        found[(state, LOWER.get(state, "house"))] = (numbers[0], numbers[1], numbers[2], numbers[3], numbers[4])
        found[(state, "senate")] = (numbers[5], numbers[6], numbers[7], numbers[8], numbers[9])
    if len(found) != 99:
        raise ValueError(f"the NCSL table has {len(found)} chambers, expected 99")
    return found


def emit() -> Path:
    sitting = members()
    printed = composition()
    counts: dict[tuple[str, str], list[int]] = {}
    for member in sitting:
        tally = counts.setdefault((str(member["state"]), str(member["chamber"])), [0, 0, 0])
        party = str(member["party"])
        tally[0 if party.startswith("Democratic") else 1 if party == "Republican" else 2] += 1
    drift: list[str] = []
    for key, (total, democrats, republicans, others, vacant) in sorted(printed.items()):
        mine = counts.get(key, [0, 0, 0])
        seated = sum(mine)
        if abs(seated - (total - vacant)) > DRIFT or seated > total:
            raise ValueError(f"{key}: OpenStates seats {seated}, NCSL {total} seats with {vacant} vacant")
        if key[0] != "NE" and max(abs(mine[0] - democrats), abs(mine[1] - republicans)) > DRIFT + 2:
            raise ValueError(f"{key}: OpenStates {mine}, NCSL {(democrats, republicans, others)}")
        if seated != total - vacant or (key[0] != "NE" and mine != [democrats, republicans, others]):
            drift.append(
                f"{key[0]} {key[1]}: OpenStates {mine} of {seated}, NCSL {[democrats, republicans, others]} of {total - vacant}"
            )
    print(f"{len(sitting)} sitting legislators; {len(drift)} chambers differ from NCSL's October table:")
    for line in drift:
        print(f"  {line}")
    rosters: dict[str, set[str]] = {}
    for key, (_, _, _, _, url, names) in CAUCUSES.items():
        page = fold(text(raw.cached(SOURCE, url).path))
        missing = sorted(name for name in names if fold(name) not in page)
        if missing:
            raise ValueError(f"{key}: {missing} are not on {url}")
        rosters[key] = {fold(name) for name in names}
    with connect() as conn:
        bodies = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'us.body' and value = any(%s)",
                [sorted({f"{member['state']}:{member['chamber']}" for member in sitting})],
            ).fetchall()
        }
        areas: dict[tuple[str, str], UUID] = {}
        for value, found, area in conn.execute(
            """select distinct on (x.value, ac.code) x.value, ac.code, a.id
               from contest c join election e on e.id = c.election_id and e.date_from >= '2018-01-01'
               join external_id x on x.entity_id = c.body_id and x.scheme = 'us.body' and x.value = any(%s)
               join area a on a.id = c.district_area_id and a.scheme_code in ('us.sldl', 'us.sldu')
               join area_code ac on ac.area_id = a.id
               order by x.value, ac.code, e.date_from desc""",
            [list(bodies)],
        ).fetchall():
            areas[(value, found)] = area
        winners: dict[tuple[str, str], list[tuple[date, UUID, UUID, str]]] = {}
        for value, found, day, contest, person, names in conn.execute(
            """select x.value, ac.code, e.date_from, c.id, cd.person_id, string_agg(distinct n.text, ' | ')
               from contest c join election e on e.id = c.election_id and e.kind <> 'primary' and e.date_from >= '2004-01-01'
               join external_id x on x.entity_id = c.body_id and x.scheme = 'us.body' and x.value = any(%s)
               join result_set r on r.contest_id = c.id and r.is_default
               join outcome o on o.result_set_id = r.id and o.kind in ('elected', 'won')
               join candidacy cd on cd.option_id = o.option_id and cd.role = 'head'
               join name n on n.entity_id = cd.person_id
               join area_code ac on ac.area_id = c.district_area_id
               group by 1, 2, 3, 4, 5""",
            [list(bodies)],
        ).fetchall():
            winners.setdefault((value, found), []).append((day, contest, person, fold(names)))
        known = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'openstates.person'").fetchall()
        )
        wikidata = dict(conn.execute("select value, entity_id from external_id where scheme = 'wikidata'").fetchall())
        identified = {
            (scheme, entity)
            for scheme, entity in conn.execute(
                "select scheme, entity_id from external_id where scheme in ('openstates.person', 'wikidata')"
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Sitting state legislators in all 50 states, with their parties, from Open States, and the Alaska Legislature's bipartisan majority caucuses",
            polity="US",
        )
        source = cs.source("Open States", "people")
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Open States",
            title="people",
            url=LANDING,
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, ARCHIVE), source)
        conference = cs.source("National Conference of State Legislatures", "State Partisan Composition")
        cs.add(
            "source",
            id=conference,
            kind="official_body",
            publisher="National Conference of State Legislatures",
            title="State Partisan Composition",
            url=NCSL,
            license=UNLICENSED,
        )
        cs.artifact(raw.cached(SOURCE, NCSL), conference)
        parties = {key: cs.key("us.party", key) for key in PARTIES.values()}
        if any(party in cs.minted for party in parties.values()):
            raise ValueError("the parties must already exist")
        caucuses: dict[str, UUID] = {}
        for key, (_, official, short, publisher, url, _) in CAUCUSES.items():
            caucus = cs.key("us.party", key)
            caucuses[key] = caucus
            if caucus in cs.minted:
                cs.add("party", id=caucus, kind="caucus", polity_id=cs.polity("US-AK"))
            cited = cs.source(publisher, "Members of the 34th Alaska State Legislature")
            cs.add(
                "source",
                id=cited,
                kind="official_body",
                publisher=publisher,
                title="Members of the 34th Alaska State Legislature",
                url=url.split("id_/", 1)[-1],
                license=UNLICENSED,
            )
            cs.artifact(raw.cached(SOURCE, url), cited)
            for name, kind in ((official, "official"), (short, "short")):
                cs.add(
                    "name",
                    entity_id=caucus,
                    text=name,
                    lang="en",
                    kind=kind,
                    is_preferred=kind == "official",
                    valid_from=SESSION,
                    valid_from_precision="day",
                    source_id=cited,
                )
        created: set[UUID] = set()
        seated: set[UUID] = set()
        unmatched: list[str] = []
        unplaced: list[str] = []
        grouped: dict[str, int] = {key: 0 for key in CAUCUSES}
        for member in sorted(sitting, key=lambda entry: str(entry["id"])):
            state, chamber, district = str(member["state"]), str(member["chamber"]), str(member["district"])
            body_key = f"{state}:{chamber}"
            body = bodies.get(body_key)
            if body is None:
                raise ValueError(f"{body_key} must already exist")
            area = areas.get((body_key, str(member["code"])))
            if area is None:
                unplaced.append(f"{body_key} {district}")
            family, given = fold(str(member["family"])), fold(str(member["given"]))
            ocd = str(member["id"])
            start = member["start"] if isinstance(member["start"], date) else None
            candidates = [
                entry
                for entry in winners.get((body_key, str(member["code"])), [])
                if re.search(rf"\b{re.escape(family)}\b", entry[3])
            ]
            people = {entry[2] for entry in candidates}
            if len(people) > 1:
                narrowed = [entry for entry in candidates if re.search(rf"\b{re.escape(given)}\b", entry[3])]
                if len({entry[2] for entry in narrowed}) == 1:
                    candidates = narrowed
            person = known.get(ocd)
            if person is None and len({entry[2] for entry in candidates}) == 1:
                person = candidates[0][2]
            if person is None and member["wikidata"] is not None:
                person = wikidata.get(str(member["wikidata"]))
            if person is None:
                person = cs.key("openstates.person", ocd)
                if person in cs.minted:
                    unmatched.append(f"{body_key} {district}: {member['name']}")
                if person in cs.minted and person not in created:
                    created.add(person)
                    cs.add("person", id=person)
                    cs.add(
                        "name",
                        entity_id=person,
                        text=str(member["name"]),
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            elif ocd not in known and ("openstates.person", person) not in identified:
                cs.add("external_id", entity_id=person, scheme="openstates.person", value=ocd)
                identified.add(("openstates.person", person))
            if (
                member["wikidata"] is not None
                and str(member["wikidata"]) not in wikidata
                and ("wikidata", person) not in identified
            ):
                cs.add("external_id", entity_id=person, scheme="wikidata", value=str(member["wikidata"]))
                identified.add(("wikidata", person))
            won = sorted(
                (day, contest)
                for day, contest, found, _ in candidates
                if found == person and (start is None or day <= start + timedelta(days=90))
            )
            seat = cs.key("us.seat", f"{body_key}:{str(member['code']).split(':', 1)[1]}")
            if seat not in seated:
                seated.add(seat)
                cs.add(
                    "seat",
                    id=seat,
                    body_id=body,
                    district_area_id=area,
                    label=f"{state} {LABELS[chamber]} {district}",
                    voting=True,
                    valid_from=None,
                    valid_to=None,
                    valid_to_precision="day",
                )
            tenure = cs.key(f"{SOURCE}.tenure", f"{ocd}:{body_key}")
            cs.add(
                "tenure",
                id=tenure,
                person_id=person,
                body_id=body,
                seat_id=seat,
                from_date=start,
                from_precision="day",
                to_date=None,
                to_precision="day",
                acquired_via="elected" if won or start is None or start.month not in range(3, 11) else "appointed",
                contest_id=won[-1][1] if won else None,
                ended_via=None,
            )
            party = str(member["party"])
            if party != "Nonpartisan":
                if party != "Independent" and party not in PARTIES:
                    raise ValueError(f"{ocd}: unknown party {party}")
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=parties[PARTIES[party]] if party in PARTIES else None,
                    valid_from=start,
                    valid_from_precision="day",
                    valid_to=None,
                    valid_to_precision="day",
                )
            for key, (chamber_key, *_) in CAUCUSES.items():
                if chamber_key == body_key and family in rosters[key]:
                    grouped[key] += 1
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="parliamentary_group",
                        party_id=caucuses[key],
                        valid_from=max(start or SESSION, SESSION),
                        valid_from_precision="day",
                        valid_to=None,
                        valid_to_precision="day",
                    )
        for key, count in grouped.items():
            if count != len(CAUCUSES[key][5]):
                raise ValueError(f"{key}: {count} sitting members matched, the roster lists {len(CAUCUSES[key][5])}")
        print(f"{len(unmatched)} legislators matched no winner in the archive and were minted")
        if unplaced:
            raise ValueError(f"{len(unplaced)} districts have no area: {unplaced[:10]}")
        return cs.write()
