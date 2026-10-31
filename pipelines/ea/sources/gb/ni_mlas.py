import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid5

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.gb.msp_wikidata import fold, seat
from ea.sources.gb.ni_assembly import DAYS
from ea.store import DATA, connect

SOURCE = "gb.ni_mlas"
VERSION = "1"
URL = "https://aims.niassembly.gov.uk/mlas/details.aspx?c=2&per={}"
API = "http://data.niassembly.gov.uk/members_json.asmx/GetAllMembers_JSON"
UNMATCHED = DATA / "tmp" / "uk-dev" / "ni_mlas_unmatched.tsv"
ELECTIONS = sorted(DAYS.values())
HONORIFICS = {
    "mr",
    "mrs",
    "ms",
    "miss",
    "dr",
    "prof",
    "professor",
    "lord",
    "lady",
    "sir",
    "dame",
    "baroness",
    "rev",
    "reverend",
    "of",
    "the",
    "mbe",
    "obe",
    "kc",
    "cbe",
    "qc",
}
PARTIES = {
    "Democratic Unionist Party": "Democratic Unionist Party",
    "Ulster Unionist Party": "Ulster Unionist Party",
    "Social Democratic and Labour Party": "Social Democratic and Labour Party",
    "Sinn Féin": "Sinn Féin",
    "Alliance Party": "Alliance Party",
    "Progressive Unionist Party": "Progressive Unionist Party",
    "United Kingdom Unionist Party": "UK Unionist Party",
    "Northern Ireland Unionist Party": "Northern Ireland Unionist Party",
    "Northern Ireland Women's Coalition": "NI Women's Coalition",
    "UK Independence Party": "UK Independence Party",
    "Green Party": "Green Party Northern Ireland",
    "United Unionist Assembly Party": "United Unionist Assembly Party",
    "Traditional Unionist Voice": "Traditional Unionist Voice",
    "People Before Profit Alliance": "People Before Profit Alliance",
    "NI21": "NI21",
    "Independent Health Coalition": "Independent Health Coalition",
    "Independent": None,
    "Independent Unionist": None,
}
OFFICES = {
    "First Minister": ("first-minister-ni", "First Minister of Northern Ireland", False),
    "Acting First Minister": ("first-minister-ni", "First Minister of Northern Ireland", True),
    "deputy First Minister": ("deputy-first-minister-ni", "deputy First Minister of Northern Ireland", False),
    "Acting deputy First Minister": ("deputy-first-minister-ni", "deputy First Minister of Northern Ireland", True),
}
SUSPENSIONS = (
    "The two offices are joint: when either holder resigns or ceases to hold office, the other ceases to hold office too, and both are filled together. "
    "No one held either office while devolution was suspended or the offices lay vacant: 11 February 2000 to 30 May 2000; for 24 hours on 11 August 2001 and again on 22 September 2001; "
    "from 15 October 2002 to 8 May 2007; from 9 January 2017 to 11 January 2020; and from 4 February 2022 to 3 February 2024. "
    "Before devolution on 2 December 1999 the Assembly had elected a First Minister (Designate) and a deputy First Minister (Designate); the Assembly's member records, which the tenures here follow, start on 2 December 1999. "
    "Spells are as the Assembly's member records print them, so one holder's continuous service can appear as several tenures split at elections."
)
IDS = (
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    48,
    49,
    50,
    51,
    52,
    53,
    54,
    55,
    56,
    57,
    58,
    59,
    60,
    61,
    62,
    63,
    64,
    65,
    66,
    67,
    68,
    69,
    70,
    71,
    72,
    73,
    74,
    75,
    76,
    77,
    78,
    79,
    80,
    81,
    82,
    83,
    84,
    85,
    86,
    87,
    88,
    89,
    90,
    91,
    92,
    93,
    94,
    95,
    96,
    97,
    98,
    99,
    100,
    101,
    102,
    103,
    104,
    105,
    106,
    107,
    108,
    109,
    110,
    111,
    112,
    113,
    114,
    115,
    116,
    117,
    118,
    119,
    120,
    121,
    122,
    123,
    124,
    125,
    126,
    127,
    128,
    129,
    130,
    131,
    132,
    133,
    134,
    135,
    136,
    137,
    138,
    139,
    140,
    141,
    142,
    143,
    144,
    145,
    146,
    147,
    148,
    149,
    150,
    151,
    152,
    153,
    154,
    155,
    166,
    172,
    180,
    193,
    194,
    195,
    196,
    197,
    198,
    199,
    200,
    201,
    202,
    203,
    204,
    205,
    206,
    207,
    208,
    209,
    210,
    211,
    212,
    213,
    214,
    215,
    216,
    217,
    218,
    219,
    247,
    256,
    258,
    259,
    268,
    269,
    271,
    272,
    273,
    274,
    277,
    281,
    287,
    288,
    289,
    290,
    291,
    292,
    293,
    294,
    295,
    296,
    297,
    298,
    299,
    300,
    301,
    302,
    303,
    304,
    305,
    306,
    307,
    308,
    309,
    1910,
    1931,
    2082,
    2150,
    2262,
    2324,
    2325,
    2326,
    2327,
    2657,
    3083,
    3328,
    3495,
    4145,
    4367,
    4465,
    4510,
    4541,
    5043,
    5223,
    5224,
    5225,
    5251,
    5307,
    5333,
    5594,
    5595,
    5654,
    5785,
    5786,
    5787,
    5789,
    5790,
    5791,
    5792,
    5793,
    5794,
    5795,
    5796,
    5797,
    5798,
    5799,
    5800,
    5801,
    5802,
    5803,
    5804,
    5805,
    5806,
    5807,
    5808,
    5809,
    5811,
    6103,
    6153,
    6154,
    6155,
    6156,
    6157,
    6158,
    6159,
    6160,
    6161,
    6162,
    6166,
    6167,
    6169,
    6170,
    6173,
    6176,
    6177,
    6178,
    6179,
    6464,
    6792,
    7409,
    7425,
    7467,
    7468,
    7534,
    7556,
    8040,
    8068,
    8070,
    8071,
    8072,
    8073,
    8074,
    8075,
    8076,
    8077,
    8078,
    8079,
    8080,
    8081,
    8082,
    8083,
    8084,
    8085,
    8086,
    8098,
    8110,
    8348,
    8538,
    8540,
    8541,
    8542,
    8652,
    8977,
    9509,
    9817,
    10076,
    10356,
)


def fetch() -> None:
    raw.fetch(SOURCE, API, suffix=".json")
    for person in IDS:
        raw.fetch(SOURCE, URL.format(person), suffix=".html")


def cells(chunk: str) -> list[list[str]]:
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", chunk, re.DOTALL):
        found = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(cell))).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        ]
        if found:
            rows.append(found)
    return rows


def day(text: str) -> date | None:
    match = re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", text)
    return date(int(match[3]), int(match[2]), int(match[1])) if match else None


def page(person: int) -> dict[str, object]:
    text = raw.cached(SOURCE, URL.format(person)).path.read_text(encoding="utf-8", errors="replace")
    marks = {
        label: text.find(f"<label>{label}</label>")
        for label in ("MLA History", "Party History", "Office/Committee History", "All Party Group History")
    }
    ends = sorted(position for position in marks.values() if position >= 0) + [len(text)]

    def section(label: str) -> list[list[str]]:
        start = marks[label]
        return cells(text[start : next(end for end in ends if end > start)]) if start >= 0 else []

    return {
        "id": person,
        "name": html.unescape(re.search(r'FullNameLabel">([^<]*)<', text)[1]).strip(),
        "mla": section("MLA History"),
        "party": section("Party History"),
        "office": section("Office/Committee History"),
    }


def tokens(name: str) -> set[str]:
    return {
        {"jr": "jnr", "junior": "jnr", "sr": "snr", "senior": "snr"}.get(word, word)
        for word in fold(name).split()
        if len(word) > 1 and word not in HONORIFICS
    }


def squash(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"(?:jnr|jr|snr|sr|junior|senior|mbe|obe|kc|cbe|qc)$", "", re.sub(r"[^a-z0-9]", "", plain))


def span(text: str) -> tuple[date | None, date | None]:
    start, _, end = text.partition(" - ")
    return day(start), day(end)


def emit() -> Path:
    members = json.loads(ET.fromstring(raw.cached(SOURCE, API).path.read_bytes()).text or "")["AllMembersList"][
        "Member"
    ]
    api = {int(row["PersonId"]): row for row in members}
    pages = [page(person) for person in IDS]
    unmatched: list[tuple[str, str]] = []
    totals: Counter[str] = Counter()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Ireland Assembly members 1998 to 2022 with seats, party spells, and co-options, and the First Ministers and deputy First Ministers",
            polity="GB",
        )
        official = cs.source("Northern Ireland Assembly", "Members' pages and Members Web Service")
        cs.add(
            "source",
            id=official,
            kind="official_body",
            publisher="Northern Ireland Assembly",
            title="Northern Ireland Assembly Information Management System: MLA details and Members Web Service",
            url="https://aims.niassembly.gov.uk/mlas/search.aspx",
            license="Open Government Licence v3.0",
            license_url="https://data.niassembly.gov.uk/licence.aspx",
            attribution="Contains Parliamentary information licensed under the Open Government Licence v3.0.",
        )
        cs.artifact(raw.cached(SOURCE, API), official)
        for person in IDS:
            cs.artifact(raw.cached(SOURCE, URL.format(person)), official)
        body = cs.key("gb.body", "ni-assembly")
        gb = cs.polity("GB-NIR")
        bank: dict[tuple[int, tuple[str, ...]], list[tuple[UUID, str, UUID | None, bool, UUID, UUID]]] = defaultdict(
            list
        )
        area_of: dict[tuple[int, tuple[str, ...]], UUID] = {}
        for when, district, area, contest, person, text, party, elected in conn.execute(
            """select e.date_from, an.text, c.district_area_id, c.id, ca.person_id, pn.text, o.party_id,
                      exists (select 1 from outcome oc join result_set rs on rs.id = oc.result_set_id
                              where oc.option_id = o.id and oc.kind = 'elected' and rs.is_default)
               from candidacy ca join contest_option o on o.id = ca.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id join name pn on pn.entity_id = ca.person_id and pn.is_preferred
               join name an on an.entity_id = c.district_area_id and an.is_preferred
               where c.body_id = %s""",
            [body],
        ).fetchall():
            key = (when.year, seat(district))
            bank[key].append((person, text, party, elected, contest, area))
            area_of[key] = area
        for key in [key for key in area_of if key[0] == 2017]:
            area_of.setdefault((2022, key[1]), area_of[key])
        known: dict[str, UUID] = {}
        for party, text in conn.execute(
            "select p.id, n.text from party p join polity y on y.id = p.polity_id join name n on n.entity_id = p.id"
            " where y.iso_code in ('GB', 'GB-NIR') order by p.id desc"
        ).fetchall():
            known[fold(text)] = party
        found = conn.execute(
            "select p.id from party p join polity y on y.id = p.polity_id join name n on n.entity_id = p.id"
            " where y.iso_code = 'IE' and n.text = 'Sinn Féin' order by p.id limit 1"
        ).fetchone()
        if found is not None:
            known[fold("Sinn Féin")] = found[0]
        parties: dict[str, UUID | None] = {}

        def party_id(label: str) -> UUID | None:
            if label not in parties:
                canonical = PARTIES[label]
                if canonical is None:
                    parties[label] = None
                elif fold(canonical) in known:
                    parties[label] = known[fold(canonical)]
                else:
                    parties[label] = cs.key("gb.party", slug(canonical))
                    if parties[label] in cs.minted:
                        cs.add("party", id=parties[label], kind="party", polity_id=gb)
                        cs.add(
                            "name",
                            entity_id=parties[label],
                            text=canonical,
                            lang="en",
                            kind="official",
                            is_preferred=True,
                        )
            return parties[label]

        def term_of(when: date) -> date:
            return max(election for election in ELECTIONS if election <= when)

        mandates: list[dict[str, object]] = []
        survivors: dict[int, UUID] = {}
        matches: dict[int, dict[UUID, int]] = {}
        claimed: dict[tuple[int, tuple[str, ...]], set[UUID]] = defaultdict(set)
        pending: dict[tuple[int, tuple[str, ...]], list[tuple[int, UUID | None]]] = defaultdict(list)
        for record in pages:
            ident = int(str(record["id"]))
            who = str(record["name"])
            first, last = (
                str(api.get(ident, {}).get("MemberFirstName", "")),
                str(api.get(ident, {}).get("MemberLastName", "")),
            )
            wanted = tokens(who) | tokens(f"{first} {last}")
            surname = squash(last or who.split()[-1])
            matches[ident] = {}
            for stamp, district, status in (row for row in record["mla"] if len(row) == 3 and day(row[0]) is not None):
                since = day(stamp)
                year = term_of(since).year
                key = (year, seat(district))
                ranked = sorted(
                    (
                        (1 if elected else 0, len(wanted & tokens(text)), person)
                        for person, text, _, elected, _, _ in bank.get(key, [])
                        if squash(text).endswith(surname)
                    ),
                    reverse=True,
                )
                if ranked and (len(ranked) == 1 or ranked[0][:2] != ranked[1][:2]):
                    matches[ident][ranked[0][2]] = year
                    claimed[key].add(ranked[0][2])
                elif (
                    not ranked
                    and since - term_of(since) <= timedelta(days=21)
                    and not status.lower().split(",")[-1].strip().startswith("replaced")
                ):
                    label = next(
                        (
                            party
                            for party, role, period in record["party"]
                            if role == "Party Member"
                            and span(period)[0] is not None
                            and abs((span(period)[0] - term_of(since)).days) <= 21
                        ),
                        None,
                    )
                    pending[key].append((ident, party_id(label) if label is not None else None))
        for key, waiting in pending.items():
            open_candidates = [
                (person, party)
                for person, _, party, elected, _, _ in bank.get(key, [])
                if elected and person not in claimed[key]
            ]
            if len(waiting) == 1 and len(open_candidates) == 1 and waiting[0][1] == open_candidates[0][1]:
                matches[waiting[0][0]][open_candidates[0][0]] = key[0]
                totals["matched by seat and party"] += 1
        text_of = {person: text for rows_ in bank.values() for person, text, *_ in rows_}
        owners: dict[UUID, list[int]] = defaultdict(list)
        for ident, found in matches.items():
            for person in found:
                owners[person].append(ident)
        for person, idents in owners.items():
            if len(idents) > 1:
                if (
                    len(
                        {
                            fold(
                                f"{api.get(ident, {}).get('MemberFirstName')} {api.get(ident, {}).get('MemberLastName')}"
                            )
                            for ident in idents
                        }
                    )
                    == 1
                ):
                    continue
                scores = {
                    ident: len(
                        tokens(
                            str(api.get(ident, {}).get("MemberFirstName", ""))
                            + " "
                            + str(api.get(ident, {}).get("MemberLastName", ""))
                            + " "
                            + str(next(r["name"] for r in pages if r["id"] == ident))
                        )
                        & tokens(text_of[person])
                    )
                    - len(
                        ({"jnr", "snr"} & tokens(text_of[person]))
                        ^ ({"jnr", "snr"} & tokens(str(next(r["name"] for r in pages if r["id"] == ident))))
                    )
                    for ident in idents
                }
                best = max(scores.values())
                keep = [ident for ident, score in scores.items() if score == best]
                for ident in idents:
                    if len(keep) != 1 or ident != keep[0]:
                        del matches[ident][person]
                        totals["match dropped for a shared surname"] += 1
        for record in pages:
            ident = int(str(record["id"]))
            who = str(record["name"])
            rows = [row for row in record["mla"] if len(row) == 3 and day(row[0]) is not None]
            found_people = matches[ident]
            if found_people:
                ordered = sorted(found_people, key=lambda person: found_people[person])
                survivors[ident] = ordered[0]
                for other in ordered[1:]:
                    cs.merge(other, ordered[0])
                    totals["merged"] += 1
            else:
                person = cs.key(f"{SOURCE}.person", str(ident))
                survivors[ident] = person
                if person in cs.minted:
                    prefix = str(api.get(ident, {}).get("MemberPrefix", ""))
                    cs.add(
                        "person",
                        id=person,
                        gender="male" if prefix == "Mr" else "female" if prefix in ("Mrs", "Ms", "Miss") else None,
                    )
                    first, last = api.get(ident, {}).get("MemberFirstName"), api.get(ident, {}).get("MemberLastName")
                    cs.add(
                        "name",
                        entity_id=person,
                        text=f"{first} {last}" if first else who,
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
                    totals["minted"] += 1
            for index, (stamp, district, status) in enumerate(rows):
                since = day(stamp)
                begin = term_of(since)
                following = next((election for election in ELECTIONS if election > begin), None)
                later = [
                    day(other[0])
                    for other in rows[index + 1 :]
                    if day(other[0]) is not None and term_of(day(other[0])) == begin
                ]
                limit = min([*later, *([following] if following else [])]) if later or following else None
                spells = []
                for party, role, period in record["party"]:
                    if role != "Party Member":
                        continue
                    start, end = span(period)
                    if (
                        start is None
                        or start < begin - timedelta(days=3)
                        or (limit is not None and start >= limit)
                        or (start < since - timedelta(days=21) and since > begin + timedelta(days=21))
                    ):
                        continue
                    spells.append((start, end, party))
                spells.sort(key=lambda spell: spell[0])
                if not spells:
                    totals["rows without a party spell"] += 1
                    continue
                mandates.append(
                    {
                        "id": ident,
                        "who": who,
                        "key": f"{ident}:{stamp}",
                        "person": survivors[ident],
                        "district": district,
                        "year": begin.year,
                        "term": begin,
                        "since": since,
                        "status": status,
                        "start": spells[0][0],
                        "end": None if any(end is None for _, end, _ in spells) else max(end for _, end, _ in spells),
                        "spells": spells,
                    }
                )
        ends: dict[date, Counter[date]] = defaultdict(Counter)
        for mandate in mandates:
            if mandate["end"] is not None:
                ends[mandate["term"]][mandate["end"]] += 1
        term_end = {term: counter.most_common(1)[0][0] for term, counter in ends.items()}
        for mandate in mandates:
            designation = str(mandate["status"]).split(",")[-1].strip()
            mandate["designation"] = designation
            mandate["elected"] = mandate["start"] - mandate["term"] <= timedelta(
                days=21
            ) and not designation.lower().startswith("replaced")
            mandate["acquired"] = "elected" if mandate["elected"] else "co_opted"
        by_seat: dict[tuple[date, tuple[str, ...]], list[dict[str, object]]] = defaultdict(list)
        for mandate in mandates:
            by_seat[(mandate["term"], seat(str(mandate["district"])))].append(mandate)
        replaced: dict[int, dict[str, object]] = {}
        slots: dict[int, int] = {}
        for group in by_seat.values():
            base = sorted((m for m in group if m["elected"]), key=lambda m: (m["start"], m["id"]))
            for number, mandate in enumerate(base, 1):
                slots[id(mandate)] = number
            for mandate in sorted((m for m in group if not m["elected"]), key=lambda m: m["start"]):
                named = tokens(str(mandate["designation"]).lower().removeprefix("replaced"))
                free = [
                    other
                    for other in group
                    if other is not mandate
                    and id(other) not in {id(value) for value in replaced.values()}
                    and other["start"] <= mandate["start"]
                ]
                nearby = [
                    other
                    for other in free
                    if other["end"] is not None
                    and mandate["start"] - timedelta(days=100) <= other["end"] <= mandate["start"] + timedelta(days=3)
                ]
                overlapping = [
                    other
                    for other in free
                    if other["end"] is None or other["end"] >= mandate["start"] - timedelta(days=100)
                ]
                narrowed = [other for other in nearby if named and named <= tokens(str(other["who"]))]
                if len(narrowed) != 1:
                    narrowed = [other for other in overlapping if named and named <= tokens(str(other["who"]))]
                if len(narrowed) != 1:
                    narrowed = [other for other in nearby if other["spells"][-1][2] == mandate["spells"][0][2]]
                if len(narrowed) == 1:
                    predecessor = narrowed[0]
                    replaced[id(mandate)] = predecessor
                    slots[id(mandate)] = slots.get(id(predecessor), 0)
                    if predecessor["end"] is None or predecessor["end"] >= mandate["start"]:
                        predecessor["capped"] = predecessor["end"]
                        predecessor["end"] = mandate["start"] - timedelta(days=1)
                else:
                    unmatched.append(
                        (
                            str(mandate["who"]),
                            f"{mandate['since']} {mandate['district']}: co-option has {len(narrowed)} possible predecessors ({mandate['designation']})",
                        )
                    )
                    slots[id(mandate)] = max(slots.values(), default=0) + 1
        for mandate in mandates:
            area = area_of.get((int(mandate["year"]), seat(str(mandate["district"]))))
            if area is None:
                unmatched.append((str(mandate["who"]), f"{mandate['since']} {mandate['district']}: no area"))
                mandate["skip"] = True
                continue
            mandate["area"] = area
            mandate["slot"] = slots.get(id(mandate), 0)
        seats: dict[tuple[object, ...], dict[str, object]] = {}
        for mandate in mandates:
            if mandate.get("skip"):
                continue
            place = (mandate["area"], mandate["term"], mandate["slot"])
            seats.setdefault(
                place,
                {
                    "label": f"{mandate['district']} seat {mandate['slot']} ({mandate['term'].year})",
                    "start": mandate["term"],
                    "end": term_end.get(mandate["term"]),
                    "area": mandate["area"],
                },
            )
        for place, row in seats.items():
            cs.add(
                "seat",
                id=cs.key(f"{SOURCE}.seat", f"{place[0]}:{place[1]}:{place[2]}"),
                body_id=body,
                district_area_id=row["area"],
                label=row["label"],
                valid_from=row["start"],
                valid_from_precision="day",
                valid_to=row["end"],
                valid_to_precision="day",
            )
        contest_of = {(year, key): bank[(year, key)][0][4] for (year, key) in bank}
        tenure_of: dict[str, UUID] = {}
        for mandate in mandates:
            if mandate.get("skip"):
                continue
            tenure = cs.key(f"{SOURCE}.tenure", str(mandate["key"]))
            tenure_of[str(mandate["key"])] = tenure
            end = mandate["end"]
            designation = str(mandate["designation"])
            closes = (
                end is not None
                and term_end.get(mandate["term"]) is not None
                and end >= term_end[mandate["term"]] - timedelta(days=3)
            )
            reason = (
                "term_end"
                if closes
                else "died"
                if designation == "Died In Office"
                else "resigned"
                if designation in ("Resigned", "Retired")
                else None
            )
            predecessor = replaced.get(id(mandate))
            cs.add(
                "tenure",
                id=tenure,
                person_id=mandate["person"],
                body_id=body,
                seat_id=cs.key(f"{SOURCE}.seat", f"{mandate['area']}:{mandate['term']}:{mandate['slot']}"),
                from_date=mandate["start"],
                from_precision="day",
                to_date=end,
                to_precision="day",
                acquired_via=mandate["acquired"],
                contest_id=contest_of.get((int(mandate["year"]), seat(str(mandate["district"])))),
                ended_via=reason,
                succeeds_tenure_id=cs.key(f"{SOURCE}.tenure", str(predecessor["key"]))
                if predecessor is not None
                else None,
            )
            totals["tenures"] += 1
            merged_spells: list[list[object]] = []
            for start, stop, party in mandate["spells"]:
                if (
                    merged_spells
                    and merged_spells[-1][2] == party
                    and merged_spells[-1][1] is not None
                    and start <= merged_spells[-1][1] + timedelta(days=1)
                ):
                    merged_spells[-1][1] = stop
                else:
                    merged_spells.append([start, stop, party])
            for index, (start, stop, party) in enumerate(merged_spells):
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=party_id(str(party)),
                    valid_from=start,
                    valid_from_precision="day",
                    valid_to=stop,
                    valid_to_precision="day",
                )
                if index > 0:
                    new = party_id(str(party))
                    cs.add(
                        "tenure_event",
                        id=uuid5(NAMESPACE, f"{SOURCE}.event:{mandate['key']}:switch:{start}"),
                        tenure_id=tenure,
                        kind="became_independent" if new is None else "switched_party",
                        date=start,
                        date_precision="day",
                        reason=f"Party history lists {party} from {start.isoformat()}.",
                        source_id=official,
                    )
            if reason in ("resigned", "died") and end is not None:
                cs.add(
                    "tenure_event",
                    id=uuid5(NAMESPACE, f"{SOURCE}.event:{mandate['key']}:{reason}"),
                    tenure_id=tenure,
                    kind=reason,
                    date=end,
                    date_precision="day",
                    reason=f"Member record: {designation}."
                    + (
                        f" The party period runs to {mandate['capped']}; the end is the day before the member who replaced them began."
                        if "capped" in mandate
                        else ""
                    ),
                    source_id=official,
                )
        offices: dict[str, UUID] = {}
        for record in pages:
            for period, position, department in record["office"]:
                if (
                    position not in OFFICES
                    or "Executive Office" not in department
                    and "First Minister" not in department
                ):
                    continue
                code, label, acting = OFFICES[position]
                if code not in offices:
                    offices[code] = cs.key("gb.office", code)
                    if offices[code] in cs.minted:
                        cs.add("office", id=offices[code], polity_id=gb, body_id=None, kind="head_of_government")
                        cs.add(
                            "name", entity_id=offices[code], text=label, lang="en", kind="official", is_preferred=True
                        )
                    cs.add("note", entity_id=offices[code], kind="context", body_md=SUSPENSIONS)
                start, stop = span(period)
                cs.add(
                    "tenure",
                    id=cs.key(f"{SOURCE}.office_tenure", f"{record['id']}:{code}:{start}"),
                    person_id=survivors[int(str(record["id"]))],
                    office_id=offices[code],
                    body_id=None,
                    from_date=start,
                    from_precision="day",
                    to_date=stop,
                    to_precision="day",
                    acquired_via="acting" if acting else "appointed",
                    ended_via=None,
                )
                totals["office_tenures"] += 1
        UNMATCHED.parent.mkdir(parents=True, exist_ok=True)
        UNMATCHED.write_text("\n".join(f"{name}\t{why}" for name, why in sorted(unmatched)) + "\n", encoding="utf-8")
        print(dict(totals), f"{len(unmatched)} listed in {UNMATCHED}")
        return cs.write()
