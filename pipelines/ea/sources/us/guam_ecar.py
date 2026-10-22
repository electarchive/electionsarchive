import struct
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import wikipedia_guam_governors as guam
from ea.store import DATA, connect

SOURCE = "us.guam_ecar"
VERSION = "1"
SITE = "https://gec.guam.gov/"
INBOX = DATA / "inbox" / "manual" / "MF-013"
FILES = [
    "ECAR Supplement 1982.pdf",
    "ECAR 1986.pdf",
    "ECAR 1994.pdf",
    "2002 ECAR.pdf",
]
ELECTIONS = [
    {
        "day": date(1982, 11, 2),
        "file": "ECAR 1986.pdf",
        "page": 123,
        "size": (1650, 1275),
        "note": "the comparative table of the 1978, 1982, and 1986 gubernatorial elections, GRAND TOTAL row, 1982 columns",
        "tickets": [
            ("Ricardo J. Bordallo", "Edward D. Reyes", "Democratic", 15199, (1004, 630, 1079, 656)),
            ("Paul M. Calvo", "Peter F. Perez", "Republican", 13797, (891, 630, 966, 656)),
        ],
    },
    {
        "day": date(1986, 11, 4),
        "file": "ECAR 1986.pdf",
        "page": 61,
        "size": (1275, 1650),
        "note": "the general election district results, GRAND TOTAL row",
        "tickets": [
            ("Ricardo J. Bordallo", "Edward D. Reyes", "Democratic", 15744, (636, 1425, 726, 1451)),
            ("Joseph F. Ada", "Frank F. Blas", "Republican", 18325, (940, 1425, 1030, 1452)),
        ],
    },
    {
        "day": date(1994, 11, 8),
        "file": "ECAR 1994.pdf",
        "page": 47,
        "size": (1275, 1650),
        "note": "the general election district results, GRAND TOTAL row",
        "tickets": [
            ("Carl T.C. Gutierrez", "Madeleine Z. Bordallo", "Democratic", 23405, (570, 1450, 660, 1475)),
            ("Thomas V.C. Tanaka", "Doris F. Brooks", "Republican", 19281, (934, 1450, 1024, 1476)),
        ],
    },
    {
        "day": date(2002, 11, 5),
        "file": "2002 ECAR.pdf",
        "page": 82,
        "size": (612, 792),
        "note": "the general election district results, All Districts row",
        "tickets": [
            ("Felix P. Camacho", "Kaleo S. Moylan", "Republican", 24309, (214, 674, 245, 685)),
            ("Robert A. Underwood", "Thomas C. Ada", "Democratic", 19559, (348, 674, 378, 685)),
        ],
    },
]


def url(file: str) -> str:
    return f"{SITE}#{file.replace(' ', '-')}"


def fetch() -> None:
    for file in FILES:
        raw.store(
            SOURCE,
            url(file),
            (INBOX / file).read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime(2026, 10, 21, tzinfo=UTC),
            media_type="application/pdf",
        )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Guam governor general elections of 1982, 1986, 1994, and 2002 from the Election Commission's comparative analysis reports",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source("Guam Election Commission", "Election Comparative Analysis Reports")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Guam Election Commission",
            title="Election Comparative Analysis Reports",
            url=SITE,
            license="Public record",
        )
        unit, area = conn.execute(
            """select u.id, u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id where p.iso_code = 'US-GU' and u.scheme_code = 'us.state'"""
        ).fetchone()
        polity = cs.polity("US-GU")
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        held = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value like '%%:GU:governor'"
            ).fetchall()
        }
        office = cs.key("us.office", "governor:GU")
        two_round = cs.key("us.electoral_system", "two_round")
        pages: dict[tuple[str, int], UUID] = {}
        artifacts: dict[str, UUID] = {}
        persons: dict[str, UUID] = {}
        for election_row in ELECTIONS:
            day = election_row["day"]
            year = day.year
            contest_key = f"{day}:GU:governor"
            if contest_key in held:
                continue
            file = election_row["file"]
            artifact = raw.cached(SOURCE, url(file))
            if file not in artifacts:
                artifacts[file] = cs.artifact(artifact, source)
            number = election_row["page"]
            if (file, number) not in pages:
                image = DATA / "pages" / SOURCE / f"{artifact.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(artifact.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                pages[(file, number)] = cs.page(artifacts[file], number)
                cs.add(
                    "source_page",
                    id=pages[(file, number)],
                    artifact_id=artifacts[file],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            election_key = f"{day}:GU"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind="general",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{year} Guam general election")
                cs.add("slug", path=f"us/gu/{day}-general", entity_id=election)
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=two_round,
                default_result_kind="counted",
            )
            name(contest, f"{year} Guam governor election")
            cs.add("slug", path=f"us/gu/{day}-general/governor", entity_id=contest)
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
                body_md=f"Territory-wide votes from the Guam Election Commission's Election Comparative Analysis Report ({file.removesuffix('.pdf')}), page {number} of the PDF: {election_row['note']}. The report is a scan, and a person saved it from the Commission's reports page in a web browser on 2026-10-21. Overvotes, undervotes, and write-ins are not in the candidate columns.",
            )
            ranked: list[tuple[int, UUID]] = []
            for candidate, mate, party, votes, box in election_row["tickets"]:
                token = slug(f"{candidate} {mate}")
                party_key = guam.PARTIES[party]
                party_id = cs.key("us.party", party_key)
                if party_key not in parties_known:
                    parties_known.add(party_key)
                    cs.add("party", id=party_id, kind="party", polity_id=polity)
                    name(party_id, party)
                option = cs.key("us.option", f"{contest_key}:{token}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=f"{candidate} and {mate}",
                )
                for role, label in (("head", candidate), ("running_mate", mate)):
                    key = slug(label)
                    if key not in persons:
                        persons[key] = cs.key(f"{guam.SOURCE}.person", f"GU:{key}")
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
                        is_independent=False,
                    )
                ranked.append((votes, option))
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
                width, height = election_row["size"]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=pages[(file, number)],
                    source_id=None,
                    x0=box[0] / width,
                    y0=box[1] / height,
                    x1=box[2] / width,
                    y1=box[3] / height,
                )
            ranked.sort(key=lambda pair: -pair[0])
            cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="elected", seats=None)
        return cs.write()
