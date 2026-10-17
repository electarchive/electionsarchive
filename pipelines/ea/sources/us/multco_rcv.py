import html
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.multco_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Multnomah County Elections Division"
SITE = "https://rcvresults.multco.us"
DAY = date(2024, 11, 5)
CONTESTS = {
    "City_of_Portland_Mayor": ("mayor", None),
    "City_of_Portland_Auditor": ("auditor", None),
    **{f"City_of_Portland_Councilor_-_District_{district}": ("council", district) for district in range(1, 5)},
}
OFFICIAL = (
    "Multnomah County Elections Division's final ranked-choice tabulation for the contest, published December 2, "
    "2024 from RCTab's summary file. Each candidate's value at round n is their running total; a candidate defeated "
    "in a round has 0 in the next. Exhausted ballots are cumulative. In council contests, a candidate who reaches the "
    "threshold is elected and their surplus moves on at a fraction of each ballot's value, carried to four decimal "
    "places; the fraction lost to rounding is counted with the exhausted ballots."
)


def fetch() -> None:
    index = httpx.get(SITE + "/", headers={"User-Agent": "Mozilla/5.0"}, timeout=60, follow_redirects=True).text
    raw.store(
        SOURCE, SITE + "/", index.encode(), suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html"
    )
    for page in CONTESTS:
        (report,) = set(re.findall(rf'href="/Reports/([0-9a-f-]+)/{re.escape(page)}">Final Results', index))
        url = f"{SITE}/Reports/{report}/{page}"
        text = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, follow_redirects=True).text
        raw.store(
            SOURCE,
            url,
            text.encode(),
            suffix=".html",
            retrieved_at=datetime.now(UTC),
            media_type="text/html",
        )
        for signed in {html.unescape(link) for link in re.findall(r'href="(https://mcdcselections[^"]+)"', text)}:
            parts = urlsplit(signed)
            if not parts.path.endswith((".json", "_report_official.pdf")):
                continue
            stable = f"{parts.scheme}://{parts.netloc}{quote(parts.path, safe="/%")}"
            response = httpx.get(f"{stable}?{parts.query}", timeout=120)
            response.raise_for_status()
            raw.store(
                SOURCE,
                stable,
                response.content,
                suffix=Path(parts.path).suffix,
                retrieved_at=datetime.now(UTC),
                media_type=response.headers.get("content-type", "application/octet-stream"),
            )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests = {}
    index = raw.cached(SOURCE, SITE + "/").path.read_text()
    base = "https://mcdcselectionsrcvprdst.blob.core.windows.net"
    for page in CONTESTS:
        (report,) = set(re.findall(rf'href="/Reports/([0-9a-f-]+)/{re.escape(page)}">Final Results', index))
        url = f"{SITE}/Reports/{report}/{page}"
        links = {
            urlsplit(html.unescape(link)).path
            for link in re.findall(r'href="(https://mcdcselections[^"]+)"', raw.cached(SOURCE, url).path.read_text())
        }
        (summary,) = [base + quote(link, safe="/%") for link in links if link.endswith(".json")]
        (statement,) = [base + quote(link, safe="/%") for link in links if link.endswith("_report_official.pdf")]
        data = json.loads(raw.cached(SOURCE, summary).path.read_text())
        threshold = float(data["config"]["threshold"])
        rounds: list[dict[str | None, float]] = []
        exhausted = 0.0
        defeated: list[str] = []
        elected: list[str] = []
        for result in data["results"]:
            tally: dict[str | None, float] = {label: float(votes) for label, votes in result["tally"].items()}
            for label in defeated:
                tally.setdefault(label, 0.0)
            tally[None] = round(exhausted, 4)
            rounds.append(tally)
            defeated = []
            for step in result["tallyResults"]:
                if "eliminated" in step:
                    defeated.append(step["eliminated"])
                if "elected" in step:
                    elected.append(step["elected"])
                exhausted += sum(
                    float(votes)
                    for target, votes in step["transfers"].items()
                    if target in ("exhausted", "residual surplus")
                )
        total = sum(rounds[0].values())
        for number, tally in enumerate(rounds, 1):
            if abs(sum(tally.values()) - total) > 0.001:
                raise ValueError(f"{page} round {number}: {sum(tally.values())} against {total}")
        seats = 1 if CONTESTS[page][0] != "council" else 3
        if len(elected) != seats:
            raise ValueError(f"{page}: {len(elected)} elected for {seats} seats")
        contests[page] = {
            "files": [url, summary, statement],
            "threshold": threshold,
            "seats": seats,
            "elected": elected,
            "rounds": [list(tally.items()) for tally in rounds],
        }
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Portland's first ranked-choice elections, November 5, 2024: mayor, auditor, and the four "
            "three-seat council districts, with every official round",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "4159000")
        whole = cs.unit("us.place", "4159000")
        parent = cs.key("us.election", f"{DAY}:OR")
        mayor = cs.key("us.office", "OR:4159000:mayor")
        council = cs.key("us.body", "OR:4159000:council")
        if {city, whole, parent, mayor, council} & cs.minted:
            raise ValueError("Portland, its mayor and council, and the 2024 Oregon general election must already exist")
        (polity,) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchone() or (
            None,
        )
        election = cs.key("us.election", f"{DAY}:OR:4159000")
        if election in cs.minted:
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=parent,
                kind="general",
                date_from=DAY,
                date_to=DAY,
                date_precision="day",
                status="held",
            )
            name(election, "2024 Portland general election")
            cs.add("slug", path=f"us/or/portland/{DAY}-general", entity_id=election)
        auditor = cs.key("us.office", "OR:4159000:auditor")
        if auditor in cs.minted:
            cs.add("office", id=auditor, polity_id=polity, kind="other")
            name(auditor, "Portland City Auditor")
        irv = cs.key("us.electoral_system", "portland:irv")
        stv = cs.key("us.electoral_system", "portland:stv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=polity,
            rules={"formula": "irv", "maxRankings": 6},
            rules_version=1,
        )
        name(irv, "Portland ranked-choice voting")
        cs.add(
            "electoral_system",
            id=stv,
            polity_id=polity,
            rules={"formula": "stv", "quota": "droop", "surplus": "weighted_inclusive_gregory", "maxRankings": 6},
            rules_version=1,
        )
        name(stv, "Portland single transferable vote")

        people: dict[tuple[str, str], set[tuple[UUID, bool]]] = {}
        for text, person, area in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id, t.area_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
               where t.area_id = any(%s)""",
            [[city, cs.area("us.county", "41051")]],
        ).fetchall():
            words = spell(text)
            if words:
                people.setdefault((words[0], words[-1]), set()).add((person, area == city))

        for page, (kind, district) in CONTESTS.items():
            contest = cs.key("us.contest", f"{DAY}:OR:portland:{kind}" + (f":{district}" if district else ""))
            seats = contests[page]["seats"]
            rounds = [{label: votes for label, votes in tally} for tally in contests[page]["rounds"]]
            area, top = city, whole
            if district:
                code = f"41:4159000:council:district-{district}"
                area, top = cs.area("us.local_district", code), cs.unit("us.local_district", code)
                if {area, top} & cs.minted:
                    raise ValueError(f"{code} must already exist")
            title = {"mayor": "Mayor", "auditor": "Auditor", "council": f"Councilor, District {district}"}[kind]
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if kind == "council" else "office",
                office_id={"mayor": mayor, "auditor": auditor}.get(kind),
                body_id=council if kind == "council" else None,
                district_area_id=area,
                magnitude=seats,
                round_number=1,
                electoral_system_id=stv if seats > 1 else irv,
                default_result_kind="counted",
                term="full",
            )
            name(
                contest,
                {"mayor": "2024 Portland mayoral election", "auditor": "2024 Portland auditor election"}.get(
                    kind, f"2024 Portland City Council election, District {district}"
                ),
            )
            cs.add(
                "slug",
                path=f"us/or/portland/{DAY}-general/" + (f"council-district-{district}" if district else kind),
                entity_id=contest,
            )
            page_url, *files = contests[page]["files"]
            title = f"November 5, 2024 City of Portland {title}: final ranked-choice results"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=page_url,
                license="Public record",
            )
            for url in (page_url, *files):
                cs.artifact(raw.cached(SOURCE, url), source)

            options: dict[str, UUID] = {}
            for label in (label for label in rounds[0] if label is not None):
                if "write in" in label.casefold():
                    options[label] = cs.key("us.option", f"{contest}:write_in:scattered")
                    cs.add(
                        "contest_option",
                        id=options[label],
                        contest_id=contest,
                        kind="write_in",
                        ballot_label="Write-in",
                    )
                    continue
                words = spell(label)
                known = people.get((words[0], words[-1]), set())
                matches = {person for person, _ in known}
                local = {person for person, here in known if here}
                if len(local) == 1:
                    matches = local
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(label))
                    if person in cs.minted and person not in {p for found in people.values() for p, _ in found}:
                        cs.add("person", id=person)
                        name(person, label)
                    people.setdefault((words[0], words[-1]), set()).add((person, True))
                options[label] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=label,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            result = cs.key(f"{SOURCE}.result_set", page)
            final = rounds[-1]
            for label, option in options.items():
                for outcome in (
                    ("won", "elected")
                    if label in contests[page]["elected"]
                    else ("lost" if final.get(label) else "eliminated",)
                ):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=OFFICIAL)
            rows = []
            for number, tally in enumerate(rounds, 1):
                for label, votes in tally.items():
                    rows.append((top, options.get(label) if label else None, 21 if label is None else 1, number, votes))
            first = rounds[0]
            rows.append((top, None, 3, 0, sum(first.values())))
            rows.append((top, None, 4, 1, sum(votes for label, votes in first.items() if label is not None)))
            for unit, option, measure, number, votes in rows:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=0,
                    value=round(votes, 4),
                )
        return cs.write()
