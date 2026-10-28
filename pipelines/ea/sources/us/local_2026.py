import html
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.sld_primaries_2026 import cased
from ea.store import DATA, connect

SOURCE = "us.local_2026"
VERSION = "1"
DAY = date(2026, 11, 3)
PARSED = DATA / "parsed" / SOURCE
FILES = {
    "06037": "https://content.lavote.gov/docs/rrcc/documents/final-list-of-qualified-candidates-to-appear-on-the-ballot9fcf3b64-1a33-4882-9e17-8cbc3b3c8d76.pdf",
    "0644000": "https://cityclerk.lacity.org/election/2026_General_Municipal_Election_List_of_Certified_Candidates.pdf",
    "06073": "https://www.sdvote.com/content/dam/rov/en/election/nov-3-2026-gubernatorial-general-election/candidate-list.pdf",
    "06059": "https://ocvote.gov/fileadmin/user_upload/data/candlog/GEN2026/candlog.pdf",
    "53033": "https://cdn.kingcounty.gov/-/media/king-county/depts/elections/how-to-vote/voters-pamphlets/2026/11/local-edition.pdf",
    "17031": "https://cboeprod.blob.core.usgovcloudapi.net/prod/2026-09/Candidate%20List_20260911-1.pdf",
    "48201": "https://hctxdnnstg.blob.core.windows.net/harrisvotes/prd/SampleBallot/2026/11-3/1126_Yellow%20Sample%20Ballot%20ENG-SPA%20for%20Website%20Posting.pdf",
    "48113": "https://assets01.aws.connect.clarityelections.com/Assets/Connect/RootPublish/dallas-tx.connect.clarityelections.com/SampleBallots/2026/261103GeneralJoint/1.pdf",
    "12086": "https://www.miamidade.gov/elections/library/2026-11-03-general-election-master-ballot.pdf",
    "3651000": "https://www.vote.nyc/sites/default/files/pdf/candidates/2026/9_11_2026%2010_26_11%20AM_General%20Contest%20List%20Web%20Version.pdf",
}
PUBLISHERS = {
    "0644000": ("CA", "Los Angeles City Clerk", "2026 General Municipal Election, November 3, 2026: Certified List of Candidates"),
    "17031": (
        "IL",
        "Chicago Board of Election Commissioners",
        "Candidate Filings in Ballot Order, November 3, 2026 (offices whose districts include part of Chicago)",
    ),
    "48201": ("TX", "Harris County Elections", "General and Special Elections, November 3, 2026: Sample Ballot"),
    "48113": ("TX", "Dallas County Elections", "General and Joint Election, November 3, 2026: Official Ballot"),
    "12086": ("FL", "Miami-Dade County Elections Department", "Official General Election Ballot, November 3, 2026"),
}
OFFICES = {
    "0644000": [
        (r"MAYOR", "mayor"),
        (r"CITY ATTORNEY", "city-attorney"),
        (r"CONTROLLER", "city-controller"),
        (r"MEMBER OF THE CITY COUNCIL, DISTRICT NO\. (\d+)", "council:{}"),
    ],
    "17031": [
        (r"County Clerk", "clerk"),
        (r"County Sheriff", "sheriff"),
        (r"County Treasurer", "treasurer"),
        (r"County Assessor", "assessor"),
        (r"County Commissioner, (\d+)\w\w District", "board-of-commissioners:district-{}"),
        (r"Board of Review, (\d+)\w\w District", "board-of-review:district-{}"),
        (
            r"Metropolitan Water Reclamation District Commissioners Full 6-Year Term",
            "reclamation:metropolitan-water-reclamation-district-of-greater-chicago:special",
        ),
        (
            r"Metropolitan Water Reclamation District Commissioners Unexpired 2-Year Term",
            "reclamation:metropolitan-water-reclamation-district-of-greater-chicago:special:2-year-term",
        ),
    ],
    "48201": [
        (r"County Judge", "county-judge"),
        (r"County Clerk", "clerk"),
        (r"District Clerk", "district-clerk"),
        (r"County Treasurer", "treasurer"),
        (r"County Attorney - Unexpired Term", "county-attorney"),
        (r"County Commissioner, Precinct (\d+)", "commissioners-court:precinct-{}"),
    ],
    "48113": [
        (r"County Judge", "county-judge"),
        (r"County Clerk", "clerk"),
        (r"District Clerk", "district-clerk"),
        (r"County Treasurer", "treasurer"),
        (r"Criminal District Attorney", "district-attorney"),
    ],
    "12086": [
        (r"County Commissioner, District (\d+)", "commission:district-{}"),
        (r"School Board Member, District (\d+)", "board-of-education:district-{}"),
    ],
}
PARTIES = {
    "Republican": "republican-party",
    "Rep": "republican-party",
    "Democratic": "democratic-party",
    "Dem": "democratic-party",
    "Libertarian": "libertarian-party",
    "Lib": "libertarian-party",
    "Green": "green-party",
    "Grn": "green-party",
}
NUMBERS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
WATERMARK = re.compile(r"^(?:[A-Z]{1,2} )+(?=\S\S)|(?: (?!JR|SR|II|IV)[A-Z]{1,2})+$")


def fetch() -> None:
    for url in FILES.values():
        try:
            raw.fetch(SOURCE, url, suffix=".pdf")
        except Exception as error:
            print(f"{url}: {error}")


def parse() -> None:
    rows: list[dict[str, object]] = []

    def office(fips: str, heading: str) -> str | None:
        for pattern, tail in OFFICES[fips]:
            found = re.fullmatch(pattern, heading.strip())
            if found is not None:
                return f"{PUBLISHERS[fips][0]}:{fips}:{tail.format(*found.groups())}"
        return None

    def add(fips: str, tail: str, heading: str, seats: int, name: str, party: str | None) -> None:
        rows.append(
            {
                "fips": fips,
                "tail": tail,
                "heading": heading.strip(),
                "seats": seats,
                "name": cased(re.sub(r"\s+", " ", name).strip()),
                "party": party,
                "unexpired": bool(re.search(r"(?i)unexpired", heading)),
            }
        )

    def text(fips: str, *flags: str) -> str:
        path = raw.cached(SOURCE, FILES[fips]).path
        return subprocess.run(["pdftotext", *flags, str(path), "-"], capture_output=True, check=True).stdout.decode()

    tail = None
    for line in text("0644000", "-layout").splitlines():
        candidate = re.match(r"^([A-Z][A-Z .,'\"()-]+?)\s{2,}\S", line)
        if line.strip() and not candidate:
            tail = office("0644000", line.strip())
        elif candidate and tail is not None:
            add("0644000", tail, "", 1, candidate[1], None)

    lines = text("17031", "-layout").splitlines()
    for index, line in enumerate(lines):
        vote = re.match(r"^\s+Vote for (?:not more than )?(\w+)\s*$", line)
        if vote is None or (tail := office("17031", lines[index - 1])) is None:
            continue
        for other in lines[index + 1 :]:
            candidate = re.match(r"^\s*\(\d+\)\s+(.+?)(?:\s+\(([^()]+)\))?\s{2,}\w+\s*$", other)
            if candidate is None:
                if other.strip() and not re.match(r"^\s*(Write-in|Ballot No\.|Page \d)", other.strip()):
                    break
                continue
            add("17031", tail, lines[index - 1], NUMBERS[vote[1]], candidate[1], PARTIES.get(str(candidate[2]).split()[0]))

    for fips in ("48201", "48113"):
        words = subprocess.run(
            ["pdftotext", "-bbox-layout", str(raw.cached(SOURCE, FILES[fips]).path), "-"], capture_output=True, check=True
        ).stdout.decode()
        pages = [
            [
                (float(x), float(y), html.unescape(word))
                for x, y, word in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)</word>', page)
            ]
            for page in words.split("<page ")[1:]
        ]
        grid: list[int] = []
        for x in sorted(
            {
                round(x)
                for page in pages
                for (x, y, word), (_, other_y, following) in zip(page, page[1:])
                if word == "Vote" and abs(y - other_y) < 1 and following in ("for", "por")
            }
        ):
            if not grid or x - grid[-1] > 40:
                grid.append(x)
        lines = []
        for page in pages:
            for index, left in enumerate(grid):
                right = grid[index + 1] - 5 if index + 1 < len(grid) else 10_000
                found_rows: dict[float, list[tuple[float, str]]] = {}
                for x, y, word in page:
                    if (index == 0 or x >= left - 5) and x < right:
                        key = next((row for row in found_rows if abs(row - y) < 2), y)
                        found_rows.setdefault(key, []).append((x, word))
                lines += [
                    WATERMARK.sub("", " ".join(word for _, word in sorted(found_rows[row])))
                    for row in sorted(found_rows)
                ]
        lines = [line for line in lines if line and not re.fullmatch(r"[A-Z]{1,2}", line)]
        for index, line in enumerate(lines):
            if (tail := office(fips, line)) is None:
                continue
            found = []
            started = False
            for other in lines[index + 1 :]:
                if other.startswith(("Vote ", "Votar", "Bỏ phiếu")) or not started and not re.search(r"[A-Z]{2}", other):
                    started = started or other.startswith("Vote ")
                    continue
                if not started:
                    continue
                if fips == "48113":
                    candidate = re.fullmatch(r"(.+?) (Rep|Dem|Lib|Grn|Ind)", other)
                    if candidate is None:
                        if found:
                            break
                        continue
                    found.append((candidate[1], PARTIES.get(candidate[2])))
                elif re.fullmatch(r"[A-Z][A-Z .,'\"-]+", other):
                    found.append((other, None))
                elif re.fullmatch(r"(\w+) Party|Independent", other) and found and found[-1][1] is None:
                    found[-1] = (found[-1][0], PARTIES.get(other.split()[0], ""))
                elif not other.startswith("Partido"):
                    break
            for name, party in found:
                if party is not None:
                    add(fips, tail, line, 1, name, party or None)

    lines = text("12086", "-layout").splitlines()
    for index, line in enumerate(lines):
        if (tail := office("12086", line)) is None:
            continue
        for other in lines[index + 1 : index + 20]:
            candidate = re.fullmatch(r"(\S.+?)\s{2,}\d+", other.strip())
            if candidate is not None:
                add("12086", tail, line, 1, candidate[1], None)
            elif other.strip() == "" and any(row["tail"] == tail for row in rows):
                break

    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["tail", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.group_by("fips").agg(pl.col("tail").n_unique().alias("contests"), pl.len().alias("candidates")))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    tails = sorted(set(frame["tail"]))
    pattern = rf"^\d{{4}}-\d\d-\d\d:({'|'.join(re.escape(tail) for tail in tails)})$"
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Local contests on the 2026-11-03 ballot in the largest jurisdictions, with candidates from the official candidate lists and sample ballots: Los Angeles city runoffs, Cook County offices (from the Chicago board's list), Harris and Dallas county offices, and Miami-Dade runoffs",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        templates: dict[str, tuple] = {}
        for row in conn.execute(
            """select distinct on (x.value) regexp_replace(x.value, '^[0-9-]+:', ''), co.id, co.kind, co.office_id, co.body_id,
                      co.district_area_id, co.seat_id, co.boundary_set_id, co.magnitude, co.term, co.electoral_system_id,
                      co.default_result_kind, el.date_from, el.polity_id, n.text, s.path, en.text, es.path,
                      regexp_replace(ex.value, '^[0-9-]+:', '')
               from external_id x join contest co on co.id = x.entity_id join entity e on e.id = co.id and e.retired_by is null
               join election el on el.id = co.election_id
               left join name n on n.entity_id = co.id and n.is_preferred and n.lang = 'en'
               left join slug s on s.entity_id = co.id and s.is_current
               left join name en on en.entity_id = el.id and en.is_preferred and en.lang = 'en'
               left join slug es on es.entity_id = el.id and es.is_current
               join external_id ex on ex.entity_id = el.id and ex.scheme = 'us.election'
               where x.scheme = 'us.contest' and x.value ~ %s and el.date_from < %s
               order by x.value, el.date_from desc""",
            [pattern, DAY],
        ).fetchall():
            if row[0] not in templates or row[12] > templates[row[0]][12]:
                templates[row[0]] = row
        known: dict[str, list[tuple[object, str]]] = {}
        for tail, person, text in conn.execute(
            """select regexp_replace(x.value, '^[0-9-]+:', ''), ca.person_id, coalesce(ca.ballot_name, o.ballot_label, pn.text)
               from external_id x join contest_option o on o.contest_id = x.entity_id
               join candidacy ca on ca.option_id = o.id join entity e on e.id = ca.id and e.retired_by is null
               left join name pn on pn.entity_id = ca.person_id and pn.is_preferred and pn.lang = 'en'
               where x.scheme = 'us.contest' and x.value ~ %s""",
            [pattern],
        ).fetchall():
            known.setdefault(tail, []).append((person, text))

        sources = {}
        elections = {}
        counts = {"contests": 0, "chained": 0, "candidates": 0, "matched": 0, "no template": 0}
        for (fips, tail), group in frame.group_by(["fips", "tail"], maintain_order=True):
            if tail not in templates:
                counts["no template"] += 1
                print(f"no template: {tail}")
                continue
            (_, previous, kind, office_id, body_id, area, seat, plan, magnitude, term, system, result_kind, held_on, polity,
             title, path, election_title, election_path, scope) = templates[tail]
            state, publisher, source_title = PUBLISHERS[fips]
            if fips not in sources:
                sources[fips] = cs.source(publisher, source_title)
                if sources[fips] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[fips],
                        kind="official_body",
                        publisher=publisher,
                        title=source_title,
                        url=FILES[fips],
                        license="Public record",
                    )
                cs.artifact(raw.cached(SOURCE, FILES[fips]), sources[fips])
            if scope not in elections:
                elections[scope] = cs.key("us.election", f"{DAY}:{scope}")
                cs.add(
                    "election",
                    id=elections[scope],
                    polity_id=polity,
                    parent_id=cs.key("us.election", f"{DAY}:{state}"),
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    status="scheduled",
                )
                name(elections[scope], re.sub(r"^\d{4} (.+?) \w+ elections?$", r"2026 \1 general election", election_title))
                cs.add("slug", path=re.sub(r"/[0-9-]+-\w+$", f"/{DAY}-general", election_path), entity_id=elections[scope])
            chained = held_on.year == DAY.year
            key = f"{DAY}:{tail}"
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[scope],
                kind=kind,
                office_id=office_id,
                body_id=body_id,
                district_area_id=area,
                seat_id=seat,
                boundary_set_id=plan,
                magnitude=int(group["seats"][0]) if kind == "body_seats" else magnitude,
                term="remainder" if group["unexpired"][0] else term,
                round_number=2 if chained else 1,
                previous_round_id=previous if chained else None,
                electoral_system_id=system,
                default_result_kind=result_kind,
            )
            text = re.sub(r"^\d{4} ", "2026 ", title)
            name(contest, text.replace(" election", " runoff election", 1) if chained else text)
            cs.add("slug", path=re.sub(r"/[0-9-]+-\w+/", f"/{DAY}-general/", path), entity_id=contest)
            counts["contests"] += 1
            counts["chained"] += chained
            for row in group.iter_rows(named=True):
                tokens = [token for token in slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", row["name"])).split("-") if token not in ("jr", "sr", "ii", "iii", "iv")]
                pool = {
                    person
                    for person, held_text in known.get(tail, [])
                    if held_text
                    and (held := [token for token in slug(held_text).split("-") if token not in ("jr", "sr", "ii", "iii", "iv")])
                    and held[-1] == tokens[-1]
                    and held[0][:1] == tokens[0][:1]
                }
                if len(pool) == 1:
                    person = next(iter(pool))
                    counts["matched"] += 1
                else:
                    person = cs.key(f"{SOURCE}.person", f"{tail}:{slug(row['name'])}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, row["name"])
                party = cs.key("us.party", row["party"]) if row["party"] else None
                option = cs.key("us.option", f"{key}:{slug(row['name'])}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", party_id=party, ballot_label=row["name"])
                candidacy = cs.key("us.candidacy", f"{key}:{slug(row['name'])}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["name"],
                    is_independent=False,
                )
                cs.add("citation", entity_id=candidacy, source_id=sources[fips], locator=None)
                counts["candidates"] += 1
        print(counts)
        return cs.write()
