import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda, govmint
from ea.store import DATA, connect

SOURCE = "us.or_swcd_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
UNEXPIRED = {("Benton Soil and Water Conservation District", "At Large 2")}
INBOX = DATA / "inbox" / "y26" / "or"
TITLE = "Certified candidates for soil and water conservation district directors, November 3, 2026 general election"
PUBLISHERS = {
    "https://re.bentoncountyor.gov/current-election/candidates/": "Benton County Elections",
    "https://multco.us/file/east_multnomah_swcd_certified_candidates.pdf/download": "Oregon Department of Agriculture",
    "https://multco.us/file/west_multnomah_swcd_certified_candidates.pdf/download": "Oregon Department of Agriculture",
    "https://www.klamathswcd.gov/files/fc3f6a186/2026+Klamath+Director+Candidates.pdf": "Oregon Department of Agriculture",
}


def fetch() -> None:
    for url in PUBLISHERS:
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            content = subprocess.run(
                ["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True
            ).stdout
            suffix = ".pdf" if "pdf" in url else ".html"
            raw.store(
                SOURCE,
                url,
                content,
                suffix=suffix,
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf" if suffix == ".pdf" else "text/html",
            )


def parse() -> None:
    rows = []
    for file in ("swcd_west.json", "swcd_east.json"):
        for entry in json.loads((INBOX / file).read_text()):
            for name in entry["candidates"]:
                rows.append(
                    {
                        "county": entry["county"],
                        "district": entry["district"],
                        "seat": entry["seat"],
                        "name": " ".join(name.split()),
                        "url": entry["source_url"],
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "seat", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        counties: dict[str, tuple[UUID, UUID, str, str]] = {}
        for polity, area, text, path, code in conn.execute(
            """select t.polity_id, a.id, n.text, s.path, c.code from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = t.polity_id and s.is_current
               where a.scheme_code = 'us.county' and left(c.code, 2) = '41'"""
        ).fetchall():
            counties[ceda._core(text)] = (polity, area, path, code)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/or/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oregon soil and water conservation district director races on the November 3, 2026 ballot, with candidates from county and ODA certified lists",
            polity="US-OR",
        )
        system = cs.key("us.electoral_system", "fptp")
        by_publisher: dict[str, UUID] = {}
        sources: dict[str, UUID] = {}
        for url, publisher in PUBLISHERS.items():
            if publisher not in by_publisher:
                by_publisher[publisher] = cs.source(publisher, TITLE)
                cs.add(
                    "source",
                    id=by_publisher[publisher],
                    kind="official_body",
                    publisher=publisher,
                    title=TITLE,
                    url=url,
                    license="Public record",
                )
            sources[url] = by_publisher[publisher]
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        parent = cs.key("us.election", f"{DAY}:OR")
        if parent in cs.minted:
            raise SystemExit("state election missing")
        made: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        elections: set[UUID] = set()
        kept = 0
        for (district, seat_label), people in frame.group_by(["district", "seat"], maintain_order=True):
            first = people.row(0, named=True)
            county = counties.get(ceda._core(f"{first['county']} County"))
            if county is None:
                print("no county:", first["county"])
                continue
            if district not in made:
                special = govmint.special(conn, cs, "OR", county, district, slugs)
                if special is None:
                    print("no district:", district)
                    continue
                made[district] = special
            polity, area, place, path, code = made[district]
            election = cs.key("us.election", f"{DAY}:OR:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{DAY.year} {place} general election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            source = sources[first["url"]]
            title = f"{place} Director {seat_label}"
            owner = cs.key("us.office", f"OR:{code}:director-{slug(seat_label)}")
            contest_key = f"{DAY}:OR:{code}:director-{slug(seat_label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/director-{slug(seat_label)}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            slugs[contest_path] = contest
            if owner in cs.minted:
                cs.add("office", id=owner, polity_id=polity, kind="other")
                cs.add("name", entity_id=owner, text=title, lang="en", kind="official", is_preferred=True)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=owner,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
                term="remainder" if (district, seat_label) in UNEXPIRED else "full",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{DAY.year} {title} election",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
            for person_row in people.iter_rows(named=True):
                ballot = person_row["name"]
                token = slug(ballot)
                person = cs.key(f"{SOURCE}.person", f"{contest_key}:{token}")
                option = cs.key("us.option", f"{contest_key}:{token}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=ballot, lang="en", kind="official", is_preferred=True)
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
                candidacy = cs.key("us.candidacy", f"{contest_key}:{token}:head")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_independent=True,
                )
                cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                kept += 1
        print(f"{kept} candidates")
        return cs.write()
