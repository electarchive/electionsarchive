import csv
import unicodedata
from collections import Counter
from datetime import date
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid5

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "tr.senato_uyeleri"
VERSION = "2"
SENATE = UUID("fa08c487-e2d8-5d2e-9fc0-7f3bce579c1f")
MEMBERS = Path(__file__).with_name("senato_uyeleri.tsv")
PUBLISHER = "Türkiye Büyük Millet Meclisi Vakfı"
TITLE = "Fahri Çöker, Türk Parlamento Tarihi: Cumhuriyet Senatosu Üyelerinin Özgeçmişleri, volumes I and II"
VOLUMES = (
    "https://acikerisim.tbmm.gov.tr/server/api/core/bitstreams/247026da-9df9-4470-a4a0-f44e64e29c82/content",
    "https://acikerisim.tbmm.gov.tr/server/api/core/bitstreams/e9bba871-26f6-478f-90c5-25e3588dd98f/content",
)
OPENED = date(1961, 10, 15)
CLOSED = date(1980, 9, 12)
ELECTED, APPOINTED = 150, 15
RENEWALS = (
    (date(1964, 6, 7), date(1973, 10, 14), date(1979, 10, 14)),
    (date(1966, 6, 5), date(1975, 10, 12)),
    (date(1968, 6, 2), date(1977, 6, 5)),
)
JOINED = {(date(1966, 6, 5), "ismen"): (date(1966, 6, 14), date(1971, 7, 20))}
NOTES = (
    "Every member of the Senate of the Republic, 1961–1980, from the two volumes of Fahri Çöker's biographies of its "
    "members, whose front-matter indexes list each member by category (natural members, members elected by "
    "election and province with party, and members chosen by the President) with a dated outcome. Natural members "
    "(the National Unity Committee and former Presidents) are `ex_officio` tenures and the President's members are "
    "`appointed` ones, as dated in the index; the book names no predecessor for an appointed seat, so only a "
    "reappointment of the same member is chained to the previous term. Elected members are the winners in YSK's "
    "results in the Resmî Gazete, each from election day until the next renewal of the province's third (the "
    "provinces renewed in 1964, 1973, and 1979; in 1966 and 1975; or in 1968 and 1977) or the Senate's dissolution "
    "on 12 September 1980, except where the index gives another end. A senator re-elected in the same province "
    "under the same surname and an overlapping given name is one person. The Senate's size is its 150 elected and "
    "15 appointed seats plus the natural members sitting at the time."
)


def fold(text: str) -> str:
    text = text.replace("I", "ı").replace("İ", "i").lower().replace("ı", "i")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def fetch() -> None:
    for url in VOLUMES:
        raw.fetch(SOURCE, url, suffix=".pdf")


def emit() -> Path:
    with open(MEMBERS, encoding="utf-8") as handle:
        rows = [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle, delimiter="\t")]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Senate of the Republic tenures 1961–1980: natural, appointed, and elected members, its size, and its dissolution",
            polity="TR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://acikerisim.tbmm.gov.tr/",
            license="No license stated",
            notes=NOTES,
        )
        for url in VOLUMES:
            cs.artifact(raw.cached(SOURCE, url), source)

        winners = conn.execute(
            """select x.value, c.id, e.date_from,
                 (select ac.code from area_code ac where ac.area_id = c.district_area_id limit 1),
                 coalesce((select m.into_id from entity_merge m where m.entity_id = cy.person_id), cy.person_id),
                 n.text, case when cy.is_independent then null else coalesce(o.party_id, cn.party_id) end, o.party_id
               from external_id x
               join contest c on c.id = x.entity_id
               join election e on e.id = c.election_id
               join result_set rs on rs.contest_id = c.id and rs.is_default
               join outcome oc on oc.result_set_id = rs.id and oc.kind = 'elected'
               join contest_option o on o.id = oc.option_id
               join candidacy cy on cy.id = coalesce(oc.candidacy_id, (select id from candidacy where option_id = o.id limit 1))
               left join candidacy_nomination cn on cn.candidacy_id = cy.id and cn.kind = 'nominated'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.scheme = 'tr.contest' and x.value like '%:senate:%'
               order by e.date_from, x.value, n.text"""
        ).fetchall()
        held = Counter((plate, day) for _, _, day, plate, *_ in winners)
        groups: dict[str, tuple[date, ...]] = {}
        for plate in {plate for plate, _ in held}:
            fits = sorted(
                (sum(held[plate, day] for day in dates) / len(dates), dates)
                for dates in RENEWALS
                if all((plate, day) in held for day in dates)
            )
            if not fits or len(fits) > 1 and fits[-1][0] == fits[-2][0]:
                raise ValueError(f"province {plate} fits renewal groups {fits}")
            groups[plate] = fits[-1][1]

        overrides: dict[tuple[date, str], dict[str, str]] = {}
        for row in rows:
            if row["acquired_via"] == "elected":
                day, _, last = row["link"].removeprefix("elected:").partition(":")
                if date.fromisoformat(day) == date.fromisoformat(row["from"]):
                    overrides[date.fromisoformat(row["from"]), fold(last)] = row
        seen_overrides: set[tuple[date, str]] = set()

        def surname(text: str) -> str:
            return fold(text.split()[-1])

        def given(text: str) -> set[str]:
            return {fold(part) for part in text.split()[:-1]}

        survivors: dict[UUID, UUID] = {}

        def find(person: UUID) -> UUID:
            while person in survivors:
                person = survivors[person]
            return person

        def join(later: UUID, earlier: UUID) -> None:
            later, earlier = find(later), find(earlier)
            if later != earlier:
                survivors[later] = earlier

        elected: list[tuple[str, UUID, date, str, UUID, str, list[tuple[date, date, UUID | None]], date, str, str]] = []
        for key, contest, day, plate, person, text, party, line in winners:
            override = overrides.get((day, surname(text)))
            if override is not None:
                if (day, surname(text)) in seen_overrides:
                    raise ValueError(f"two winners on {day} named {text}")
                seen_overrides.add((day, surname(text)))
                end, ended = date.fromisoformat(override["to"]), override["ended_via"]
            else:
                end = next((renewal for renewal in groups[plate] if renewal > day), CLOSED)
                ended = "dissolved" if end == CLOSED else "term_end"
            joined, left = JOINED.get((day, surname(text)), (end, end))
            spans = [
                (start, stop, owner)
                for start, stop, owner in ((day, joined, party), (joined, left, line), (left, end, party))
                if stop > start
            ]
            elected.append(
                (
                    key,
                    contest,
                    day,
                    plate,
                    person,
                    text,
                    spans,
                    end,
                    ended,
                    "" if override is None else override["note"],
                )
            )
        if missing := set(overrides) - seen_overrides:
            raise ValueError(f"index rows with no winner: {sorted(missing)}")

        by_plate: dict[tuple[str, str], list[int]] = {}
        for index, (_, _, _, plate, _, text, *_) in enumerate(elected):
            by_plate.setdefault((plate, surname(text)), []).append(index)
        for indexes in by_plate.values():
            for position, later in enumerate(indexes):
                for earlier in indexes[:position]:
                    first, second = elected[earlier], elected[later]
                    if given(first[5]) & given(second[5]) and first[7] <= second[2]:
                        join(second[4], first[4])
                        break

        def winner(link: str) -> UUID:
            day, _, name = link.removeprefix("elected:").partition(":")
            found = {
                find(person)
                for _, _, held_on, _, person, text, *_ in elected
                if held_on == date.fromisoformat(day) and surname(text) == fold(name)
            }
            if len(found) != 1:
                raise ValueError(f"{link} matches {len(found)} winners")
            return found.pop()

        for row in rows:
            if row["acquired_via"] == "elected" and not row["link"].startswith(f"elected:{row['from']}:"):
                join(winner(f"elected:{row['from']}:{row['name'].split()[-1]}"), winner(row["link"]))

        def member(row: dict[str, str]) -> str:
            return (
                row["link"].removeprefix("sicil:") if row["link"].startswith("sicil:") else row["sicil"] or row["name"]
            )

        appointed = [row for row in rows if row["acquired_via"] != "elected"]
        names: dict[str, str] = {}
        links: dict[str, set[str]] = {}
        for row in appointed:
            names.setdefault(member(row), row["name"])
            if row["link"] and not row["link"].startswith("sicil:"):
                links.setdefault(member(row), set()).add(row["link"])
        people: dict[str, UUID] = {}
        for key, text in names.items():
            items = sorted(
                link.removeprefix("wikidata:") for link in links.get(key, ()) if link.startswith("wikidata:")
            )
            ballots = sorted(link for link in links.get(key, ()) if link.startswith("elected:"))
            if items:
                person = cs.key("wikidata", items[0])
            elif ballots:
                person = winner(ballots[0])
            else:
                person = cs.key(f"{SOURCE}.person", key)
            if person in cs.minted:
                cs.add("person", id=person)
                cs.add("name", entity_id=person, text=text, lang="tr", kind="official", is_preferred=True)
            for ballot in ballots if items else ballots[1:]:
                join(winner(ballot), person)
            people[key] = person

        for later, earlier in survivors.items():
            cs.merge(later, find(earlier))

        sizes: list[tuple[date, date]] = []
        previous: dict[UUID, UUID] = {}
        for row in sorted(appointed, key=lambda row: (row["from"], row["name"])):
            person = find(people[member(row)])
            start, end = date.fromisoformat(row["from"]), date.fromisoformat(row["to"])
            tenure = cs.key(f"{SOURCE}.tenure", f"{row['acquired_via']}:{member(row)}:{start}")
            cs.add(
                "tenure",
                id=tenure,
                person_id=person,
                office_id=None,
                seat_id=None,
                body_id=SENATE,
                from_date=start,
                from_precision="day",
                to_date=end,
                to_precision="day",
                acquired_via=row["acquired_via"],
                contest_id=None,
                ended_via=row["ended_via"],
                resumes_tenure_id=None,
                succeeds_tenure_id=previous.get(person) if row["acquired_via"] == "appointed" else None,
            )
            cs.add("citation", entity_id=tenure, source_id=source, locator={"member": row["sicil"] or row["name"]})
            if row["note"]:
                cs.add("note", entity_id=tenure, kind="context", body_md=row["note"])
            if row["acquired_via"] == "appointed":
                previous[person] = tenure
            else:
                sizes.append((start, end))

        for key, contest, day, plate, person, text, spans, end, ended, note in elected:
            tenure = cs.key(f"{SOURCE}.tenure", f"elected:{key}:{fold(text)}")
            cs.add(
                "tenure",
                id=tenure,
                person_id=find(person),
                office_id=None,
                seat_id=None,
                body_id=SENATE,
                from_date=day,
                from_precision="day",
                to_date=end,
                to_precision="day",
                acquired_via="elected",
                contest_id=contest,
                ended_via=ended,
                resumes_tenure_id=None,
                succeeds_tenure_id=None,
            )
            if note:
                cs.add("note", entity_id=tenure, kind="context", body_md=note)
                cs.add("citation", entity_id=tenure, source_id=source, locator={"member": text})
            for start, stop, party in spans:
                cs.add(
                    "tenure_affiliation",
                    tenure_id=tenure,
                    kind="party",
                    party_id=party,
                    valid_from=start,
                    valid_from_precision="day",
                    valid_to=stop,
                    valid_to_precision="day",
                )

        marks = sorted({OPENED, CLOSED, *(day for span in sizes for day in span if OPENED < day < CLOSED)})
        spans: list[tuple[date, date, int]] = []
        for start, end in pairwise(marks):
            seats = ELECTED + APPOINTED + sum(1 for began, ended in sizes if began <= start and ended > start)
            if spans and spans[-1][2] == seats:
                spans[-1] = (spans[-1][0], end, seats)
            else:
                spans.append((start, end, seats))
        for start, end, seats in spans:
            cs.add("body_size", body_id=SENATE, seats=seats, valid_from=start, valid_to=end)
        for category, seats in (("elected", ELECTED), ("appointed", APPOINTED)):
            cs.add("body_size_part", body_id=SENATE, category=category, seats=seats, valid_from=OPENED, valid_to=CLOSED)
        cs.add(
            "body_event",
            id=uuid5(SENATE, "dissolved"),
            body_id=SENATE,
            kind="dissolved",
            date=CLOSED,
            date_precision="day",
            source_id=source,
        )
        print(
            f"{len(elected)} elected tenures, {len(appointed)} natural and appointed, "
            f"{len(survivors)} persons merged, sizes {[(str(start), seats) for start, _, seats in spans]}",
            Counter(ended for *_, ended, _ in elected),
        )
        return cs.write()
