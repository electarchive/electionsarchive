import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import wikipedia_vi_governors as wikipedia
from ea.store import DATA, connect

SOURCE = "us.vi_recap_1978"
VERSION = "1"
URL = "https://vivote.gov/wp-content/uploads/2024/03/1978-Primary-and-General-Election-Certification-Reports.pdf"
PUBLISHER = "Election System of the Virgin Islands"
TITLE = "1978 Primary and General Election Certification Reports"
DAY = date(1978, 11, 7)
PAGE = 31
SIZE = (850, 1400)
TICKETS = [
    ("Juan Luis", "Henry A. Millin", None, 12100, (5530, 6570), (530, 403, 735, 424)),
    ("Ron de Lugo", "Eric E. Dawson", "democratic-party", 8111, (5011, 3100), (530, 463, 735, 484)),
]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virgin Islands 1978 governor general election from the Supervisor of Elections' certification report",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        artifact = cs.artifact(raw.cached(SOURCE, URL), source)
        unit, area = conn.execute(
            """select u.id, u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id where p.iso_code = 'US-VI' and u.scheme_code = 'us.state'"""
        ).fetchone()
        polity = cs.polity("US-VI")
        contest_key = f"{DAY}:VI:governor"
        if conn.execute(
            "select 1 from external_id where scheme = 'us.contest' and value = %s", [contest_key]
        ).fetchone():
            raise ValueError("the 1978 Virgin Islands governor contest is already held")
        image = DATA / "pages" / SOURCE / f"{raw.cached(SOURCE, URL).sha256}-{PAGE}.png"
        if not image.exists():
            image.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "100",
                    "-png",
                    "-f",
                    str(PAGE),
                    "-l",
                    str(PAGE),
                    "-singlefile",
                    str(raw.cached(SOURCE, URL).path),
                    str(image.with_suffix("")),
                ],
                check=True,
            )
        width, height = struct.unpack(">II", image.read_bytes()[16:24])
        if (width, height) != SIZE:
            raise ValueError(f"page size {(width, height)}")
        page = cs.page(artifact, PAGE)
        cs.add(
            "source_page",
            id=page,
            artifact_id=artifact,
            page_number=PAGE,
            image_key=f"pages/{SOURCE}/{image.name}",
            width=width,
            height=height,
        )
        election = cs.key("us.election", f"{DAY}:VI")
        cs.add(
            "election",
            id=election,
            polity_id=polity,
            kind="general",
            date_from=DAY,
            date_to=DAY,
            date_precision="day",
            status="held",
        )
        name(election, "1978 Virgin Islands general election")
        cs.add("slug", path=f"us/vi/{DAY}-general", entity_id=election)
        contest = cs.key("us.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=cs.key("us.office", "governor:VI"),
            district_area_id=area,
            term="full",
            magnitude=1,
            round_number=1,
            previous_round_id=None,
            electoral_system_id=cs.key("us.electoral_system", "two_round"),
            default_result_kind="counted",
        )
        name(contest, "1978 Virgin Islands governor election")
        cs.add("slug", path=f"us/vi/{DAY}-general/governor", entity_id=contest)
        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=1,
        )
        cs.add(
            "note",
            entity_id=result_set,
            kind="methodology",
            body_md=f"Territory-wide votes are the sum of the two district totals, St. Thomas-St. John and St. Croix, in the Supervisor of Elections' summary of the 1978 general election (page {PAGE} of the PDF, a scan; the winner's territory total of 12,100 is also printed on page 3). The St. Croix polling-place table (pages 9 to 11) closes on the same district totals. The English Wikipedia's article gives 12,100 and 8,111 as well but prints island subtotals for Luis that sum to 11,900. Polling-place returns are on pages 9 to 11 and are not loaded. Write-ins are not in the candidate columns.",
        )
        persons: dict[str, UUID] = {}
        ranked: list[tuple[int, UUID]] = []
        for candidate, mate, party_key, votes, parts, box in TICKETS:
            if sum(parts) != votes:
                raise ValueError(candidate)
            token = slug(f"{candidate} {mate}")
            option = cs.key("us.option", f"{contest_key}:{token}")
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=cs.key("us.party", party_key) if party_key else None,
                ballot_label=f"{candidate} and {mate}",
            )
            for role, label in (("head", candidate), ("running_mate", mate)):
                key = slug(label)
                if key not in persons:
                    persons[key] = cs.key(f"{wikipedia.SOURCE}.person", f"VI:{key}")
                    if persons[key] in cs.minted:
                        cs.add("person", id=persons[key])
                        name(persons[key], label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{token}:{role}"),
                    person_id=persons[key],
                    option_id=option,
                    role=role,
                    ballot_name=label if role == "head" else None,
                    is_independent=party_key is None,
                )
            ranked.append((votes, option))
            fact = {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": 1,
                "count_round": 0,
            }
            cs.add("result_value", **fact, flags=0, value=votes)
            cs.add(
                "value_locator",
                **fact,
                page_id=page,
                source_id=None,
                x0=box[0] / width,
                y0=box[1] / height,
                x1=box[2] / width,
                y1=box[3] / height,
            )
        ranked.sort(key=lambda pair: -pair[0])
        cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="elected", seats=None)
        return cs.write()
