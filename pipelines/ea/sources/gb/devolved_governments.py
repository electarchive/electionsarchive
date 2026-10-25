import time
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.devolved_governments"
VERSION = "2"
WIKI = "https://en.wikipedia.org/w/api.php?format=json&formatversion=2&action=parse&prop=wikitext%7Crevid&oldid="
LAB = UUID("9b4f1b0f-ec6f-56e4-8513-f44cf208b2ba")
LD = UUID("09417919-fdab-5547-ac3a-39f5b8e75603")
SNP = UUID("4ab50adb-43e2-51e0-9d2e-ec069b9c5086")
GREENS = UUID("2f10e364-6b77-5b95-9b85-84561dfb235c")
PLAID = UUID("de21f091-a168-54e6-bf61-1f4f4732998e")
DUP = UUID("31366233-b7b6-553c-a6bc-4b82bcc40e71")
SF = UUID("01a13bd3-6475-7344-8c51-dd5068c68834")
UUP = UUID("b1402dfb-7f85-53f1-a9a6-15372fe29396")
SDLP = UUID("b3f8a618-1c9c-569c-a5c0-15dba4be615e")
APNI = UUID("bc5c4442-2dc7-504a-a55f-17337893cab8")
PAGES = {
    "Dewar government": 1375893594,
    "McLeish government": 1373024858,
    "First McConnell government": 1368851144,
    "Second McConnell government": 1375893101,
    "First Salmond government": 1375892961,
    "Second Salmond government": 1375892813,
    "First Sturgeon government": 1375892678,
    "Second Sturgeon government": 1368851557,
    "Third Sturgeon government": 1375892579,
    "First Yousaf government": 1375892395,
    "Second Yousaf government": 1375892114,
    "First Swinney government": 1376256913,
    "Second Swinney government": 1381335566,
    "Michael administration": 1378999740,
    "Interim Rhodri Morgan administration": 1381556220,
    "First Rhodri Morgan government": 1381556223,
    "Second Rhodri Morgan government": 1381798754,
    "Third Rhodri Morgan government": 1370675611,
    "Fourth Rhodri Morgan government": 1380574420,
    "First Jones government": 1375890868,
    "Second Jones government": 1375890473,
    "Third Jones government": 1375890712,
    "First Drakeford government": 1375890330,
    "Second Drakeford government": 1379004223,
    "Gething government": 1379004084,
    "Eluned Morgan government": 1376257355,
    "Ap Iorwerth government": 1380288883,
    "Executive of the 1st Northern Ireland Assembly": 1354130816,
    "Executive of the 3rd Northern Ireland Assembly": 1375741053,
    "Executive of the 4th Northern Ireland Assembly": 1381729960,
    "Executive of the 5th Northern Ireland Assembly": 1349777990,
    "Executive of the 6th Northern Ireland Assembly": 1349778468,
    "Executive of the 7th Northern Ireland Assembly": 1378930267,
    "Deputy First Minister of Scotland": 1374913269,
    "Deputy First Minister of Wales": 1373661815,
}
OFFICES = {
    "scotland": ("GB-SCT", ("gb.office", "first-minister-scotland")),
    "wales": ("GB-WLS", ("gb.office", "first-minister-wales")),
    "northern-ireland": ("GB-NIR", ("gb.office", "first-minister-ni")),
}
GOVERNMENTS = {
    "scotland": [
        ("Dewar government", None, date(1999, 5, 17), "1999-05-06", [(LAB, "head"), (LD, "member")]),
        ("McLeish government", None, date(2000, 10, 27), "1999-05-06", [(LAB, "head"), (LD, "member")]),
        ("First McConnell government", None, date(2001, 11, 27), "1999-05-06", [(LAB, "head"), (LD, "member")]),
        ("Second McConnell government", None, date(2003, 5, 20), "2003-05-01", [(LAB, "head"), (LD, "member")]),
        ("First Salmond government", None, date(2007, 5, 17), "2007-05-03", [(SNP, "head")]),
        ("Second Salmond government", None, date(2011, 5, 19), "2011-05-05", [(SNP, "head")]),
        ("First Sturgeon government", None, date(2014, 11, 20), "2011-05-05", [(SNP, "head")]),
        ("Second Sturgeon government", None, date(2016, 5, 18), "2016-05-05", [(SNP, "head")]),
        ("Third Sturgeon government", None, date(2021, 5, 19), "2021-05-06", [(SNP, "head"), (GREENS, "member")]),
        ("First Yousaf government", None, date(2023, 3, 29), "2021-05-06", [(SNP, "head"), (GREENS, "member")]),
        ("Second Yousaf government", None, date(2024, 4, 25), "2021-05-06", [(SNP, "head")]),
        ("First Swinney government", None, date(2024, 5, 8), "2021-05-06", [(SNP, "head")]),
        ("Second Swinney government", None, date(2026, 5, 20), "2026-05-07", [(SNP, "head")]),
    ],
    "wales": [
        ("Michael administration", None, date(1999, 5, 12), "1999-05-06", [(LAB, "head")]),
        ("Interim Rhodri Morgan administration", None, date(2000, 2, 9), "1999-05-06", [(LAB, "head")]),
        ("First Rhodri Morgan government", None, date(2000, 10, 16), "1999-05-06", [(LAB, "head"), (LD, "member")]),
        ("Second Rhodri Morgan government", None, date(2003, 5, 1), "2003-05-01", [(LAB, "head")]),
        ("Third Rhodri Morgan government", None, date(2007, 5, 26), "2007-05-03", [(LAB, "head")]),
        ("Fourth Rhodri Morgan government", None, date(2007, 7, 19), "2007-05-03", [(LAB, "head"), (PLAID, "member")]),
        ("First Jones government", None, date(2009, 12, 10), "2007-05-03", [(LAB, "head"), (PLAID, "member")]),
        ("Second Jones government", None, date(2011, 5, 11), "2011-05-05", [(LAB, "head")]),
        ("Third Jones government", None, date(2016, 5, 19), "2016-05-05", [(LAB, "head"), (LD, "member")]),
        ("First Drakeford government", None, date(2018, 12, 13), "2016-05-05", [(LAB, "head"), (LD, "member")]),
        ("Second Drakeford government", None, date(2021, 5, 13), "2021-05-06", [(LAB, "head"), (PLAID, "support")]),
        ("Gething government", None, date(2024, 3, 21), "2021-05-06", [(LAB, "head"), (PLAID, "support")]),
        ("Eluned Morgan government", None, date(2024, 8, 6), "2021-05-06", [(LAB, "head")]),
        ("Ap Iorwerth government", None, date(2026, 5, 13), "2026-05-07", [(PLAID, "head")]),
    ],
    "northern-ireland": [
        (
            "Executive of the 1st Northern Ireland Assembly",
            None,
            date(1999, 12, 2),
            "1998-06-25",
            [(UUP, "head"), (SDLP, "head"), (DUP, "member"), (SF, "member")],
        ),
        (
            "Executive of the 3rd Northern Ireland Assembly",
            "Paisley",
            date(2007, 5, 8),
            "2007-03-07",
            [(DUP, "head"), (SF, "head"), (UUP, "member"), (SDLP, "member")],
        ),
        (
            "Executive of the 3rd Northern Ireland Assembly",
            "Robinson",
            date(2008, 6, 5),
            "2007-03-07",
            [(DUP, "head"), (SF, "head"), (UUP, "member"), (SDLP, "member"), (APNI, "member")],
        ),
        (
            "Executive of the 4th Northern Ireland Assembly",
            "Robinson",
            date(2011, 5, 16),
            "2011-05-05",
            [(DUP, "head"), (SF, "head"), (UUP, "member"), (SDLP, "member"), (APNI, "member")],
        ),
        (
            "Executive of the 4th Northern Ireland Assembly",
            "Foster",
            date(2016, 1, 11),
            "2011-05-05",
            [(DUP, "head"), (SF, "head"), (SDLP, "member"), (APNI, "member")],
        ),
        (
            "Executive of the 5th Northern Ireland Assembly",
            None,
            date(2016, 5, 26),
            "2016-05-05",
            [(DUP, "head"), (SF, "head")],
        ),
        (
            "Executive of the 6th Northern Ireland Assembly",
            "Foster",
            date(2020, 1, 11),
            "2017-03-02",
            [(DUP, "head"), (SF, "head"), (SDLP, "member"), (UUP, "member"), (APNI, "member")],
        ),
        (
            "Executive of the 6th Northern Ireland Assembly",
            "Givan",
            date(2021, 6, 17),
            "2017-03-02",
            [(DUP, "head"), (SF, "head"), (SDLP, "member"), (UUP, "member"), (APNI, "member")],
        ),
        (
            "Executive of the 7th Northern Ireland Assembly",
            None,
            date(2024, 2, 3),
            "2022-05-05",
            [(SF, "head"), (DUP, "head"), (APNI, "member"), (UUP, "member")],
        ),
    ],
}
APPOINTED = {
    date(2001, 11, 27): date(2001, 11, 22),
    date(2024, 3, 21): date(2024, 3, 20),
    date(2026, 5, 13): date(2026, 5, 12),
}
ORDINALS = {"First": "I", "Second": "II", "Third": "III", "Fourth": "IV"}
SEATS = """with recursive tree(id) as (
        select entity_id from slug where path = %s
        union all select e.id from election e join tree t on e.parent_id = t.id
    )
    select co.party_id, sum(coalesce(o.seats, 1))::int from tree
    join result_set r on r.election_id = tree.id and r.is_default
    join outcome o on o.result_set_id = r.id and o.kind = 'elected'
    join contest_option co on co.id = o.option_id
    where co.party_id is not null group by 1"""
ENDED = {
    date(1999, 12, 2): date(2002, 10, 14),
    date(2016, 5, 26): date(2017, 1, 16),
    date(2021, 6, 17): date(2022, 2, 4),
}
NOTES = {
    "Executive of the 1st Northern Ireland Assembly": "Devolution was suspended from 11 February to 30 May 2000, and "
    "for a day each on 10 August and 22 September 2001; the Executive ended with the suspension of 14 October 2002, "
    "and direct rule followed until 8 May 2007.",
    "Executive of the 5th Northern Ireland Assembly": "Claire Sugden, an independent, was Minister of Justice. The "
    "Executive ended with Martin McGuinness's resignation as deputy First Minister, and none sat again until 11 "
    "January 2020.",
    "Executive of the 6th Northern Ireland Assembly": "The Executive ended with Paul Givan's resignation as First "
    "Minister, and none sat again until 3 February 2024.",
    "Third Jones government": "Kirsty Williams, the Liberal Democrats' only member, sat in the cabinet, as did Dafydd "
    "Elis-Thomas, an independent, from 2017.",
    "First Drakeford government": "Kirsty Williams, the Liberal Democrats' only member, and Dafydd Elis-Thomas, an "
    "independent, sat in the cabinet.",
    "Second Drakeford government": "Plaid Cymru supported the government under the co-operation agreement of 1 "
    "December 2021, without ministers.",
    "Gething government": "Plaid Cymru ended the co-operation agreement on 17 May 2024.",
    "Third Sturgeon government": "The Scottish Greens joined the government under the Bute House Agreement in "
    "August 2021, with two junior ministers.",
    "First Yousaf government": "Humza Yousaf ended the Bute House Agreement on 25 April 2024.",
    "First Salmond government": "The Scottish Greens backed Alex Salmond's election as First Minister under an "
    "agreement with the SNP, without ministers.",
}
DEPUTIES = {
    "scotland": [
        (("wikidata", "Q333807"), date(1999, 5, 19), date(2005, 6, 23)),
        (("mnis", "4239"), date(2005, 6, 27), date(2007, 5, 17)),
        (("wikidata", "Q467112"), date(2007, 5, 17), date(2014, 11, 19)),
        (("wikidata", "Q333811"), date(2014, 11, 21), date(2023, 3, 28)),
        (("wikidata", "Q444675"), date(2023, 3, 29), date(2024, 5, 8)),
        (("wikidata", "Q24039479"), date(2024, 5, 8), date(2026, 5, 20)),
        (("wikidata", "Q24026044"), date(2026, 5, 20), None),
    ],
    "wales": [
        (("wikidata", "Q336526"), date(2000, 10, 16), date(2001, 7, 6)),
        (("wikidata", "Q1687064"), date(2001, 7, 6), date(2002, 6, 13)),
        (("wikidata", "Q336526"), date(2002, 6, 13), date(2003, 5, 8)),
        (("wikidata", "Q3396810"), date(2007, 7, 11), date(2011, 5, 13)),
        (("wikidata", "Q691388"), date(2024, 8, 6), date(2026, 5, 12)),
        (("wikidata", "Q106766817"), date(2026, 5, 13), None),
    ],
}
ACTING = {("wikidata", "Q1687064")}


def fetch() -> None:
    for revision in PAGES.values():
        try:
            raw.cached(SOURCE, f"{WIKI}{revision}")
        except LookupError:
            raw.fetch(SOURCE, f"{WIKI}{revision}", suffix=".json")
            time.sleep(1)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governments of Scotland, Wales, and Northern Ireland since devolution, with their parties, and "
            "the deputy First Ministers of Scotland and Wales",
            polity="GB",
        )
        sources: dict[str, UUID] = {}
        for title, revision in PAGES.items():
            sources[title] = cs.source("Wikipedia", title)
            cs.add(
                "source",
                id=sources[title],
                kind="community",
                publisher="Wikipedia",
                title=title,
                url=f"https://en.wikipedia.org/w/index.php?oldid={revision}",
                license="CC BY-SA 4.0",
                license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            )
            cs.artifact(raw.cached(SOURCE, f"{WIKI}{revision}"), sources[title])
        heads = {
            (office, start, end, person)
            for office, start, end, person in conn.execute(
                """select t.office_id, t.from_date, t.to_date, t.person_id from tenure t
                   join entity e on e.id = t.id and e.retired_by is null where t.office_id is not null"""
            ).fetchall()
        }
        for nation, (code, office_key) in OFFICES.items():
            polity = cs.polity(code)
            office = cs.key(*office_key)
            if office in cs.minted:
                raise ValueError(f"no office {office_key}")
            rows = GOVERNMENTS[nation]
            previous = None
            shorts: list[tuple[UUID, str]] = []
            for index, (title, head_name, formed, held, parties) in enumerate(rows):
                government = cs.key(f"{SOURCE}.government", f"{nation}:{formed}")
                start = APPOINTED.get(formed, formed)
                end = ENDED.get(formed) or (
                    APPOINTED.get(rows[index + 1][2], rows[index + 1][2]) if index + 1 < len(rows) else None
                )
                head = [
                    person
                    for held_office, begin, stop, person in heads
                    if held_office == office and begin <= start and (stop is None or start < stop)
                ]
                if len(set(head)) != 1:
                    raise ValueError(f"{title}: First Minister on {start} is {head}")
                election = conn.execute(
                    "select entity_id from slug where path = %s", [f"gb/{nation}/{held}-general"]
                ).fetchone()
                if election is None:
                    raise ValueError(f"no election gb/{nation}/{held}-general")
                cs.add(
                    "government",
                    id=government,
                    polity_id=polity,
                    office_id=office,
                    head_person_id=head[0],
                    from_date=start,
                    to_date=end,
                    election_id=election[0],
                    previous_government_id=previous,
                    caretaker=False,
                    source_id=sources[title],
                )
                cs.add(
                    "name",
                    entity_id=government,
                    text=(f"{title} ({head_name})" if head_name else title).replace("Ap Iorwerth", "ap Iorwerth"),
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                words = title.replace("Ap Iorwerth", "ap Iorwerth").split()
                if nation == "northern-ireland":
                    full = conn.execute(
                        "select text from name where entity_id = %s and is_preferred", [head[0]]
                    ).fetchone()
                    if full is None:
                        raise ValueError(f"{title}: First Minister {head[0]} has no name")
                    shorts.append((government, full[0].split()[-1]))
                elif words[0] in ORDINALS:
                    shorts.append((government, f"{' '.join(words[1:-1])} {ORDINALS[words[0]]}"))
                elif words[0] == "Interim":
                    shorts.append((government, f"{' '.join(words[1:-1])} (interim)"))
                else:
                    shorts.append((government, " ".join(words[:-1])))
                seats = dict(conn.execute(SEATS, [f"gb/{nation}/{held}-general"]).fetchall())
                for party, role in parties:
                    if party not in seats:
                        raise ValueError(f"{title}: no seats for {party} at {held}")
                    cs.add("government_party", government_id=government, party_id=party, role=role, seats=seats[party])
                if title in NOTES:
                    cs.add("note", entity_id=government, kind="context", body_md=NOTES[title])
                cs.add("citation", entity_id=government, source_id=sources[title])
                previous = government
            numerals = list(ORDINALS.values())
            for government, short in shorts:
                same = [other for other, text in shorts if text == short]
                text = f"{short} {numerals[same.index(government)]}" if len(same) > 1 else short
                cs.add("name", entity_id=government, text=text, lang="en", kind="short", is_preferred=False)
        for nation, label in (
            ("scotland", "Deputy First Minister of Scotland"),
            ("wales", "Deputy First Minister of Wales"),
        ):
            office = cs.key("gb.office", f"deputy-first-minister-{nation}")
            if office in cs.minted:
                cs.add("office", id=office, polity_id=cs.polity(OFFICES[nation][0]), body_id=None, kind="other")
                cs.add("name", entity_id=office, text=label, lang="en", kind="official", is_preferred=True)
            for person_key, start, end in DEPUTIES[nation]:
                person = cs.key(*person_key)
                if person in cs.minted:
                    raise ValueError(f"no person {person_key}")
                tenure = cs.key(f"{SOURCE}.tenure", f"{nation}:{person_key[1]}:{start}")
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    office_id=office,
                    body_id=None,
                    from_date=start,
                    from_precision="day",
                    to_date=end,
                    to_precision="day",
                    acquired_via="acting" if person_key in ACTING else "appointed",
                    ended_via=None if end is None else "term_end",
                )
                cs.add("citation", entity_id=tenure, source_id=sources[label])
        return cs.write()
