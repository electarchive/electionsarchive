import json
import struct
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.westbrook_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "City of Westbrook City Clerk"
REPORT = "https://www.westbrookmaine.gov/DocumentCenter/View/4997"
TITLE = "November 5, 2024 official results, State General & Referendum Election and Municipal Election"
PAGE = 5
TRANSCRIBED = {
    "Morse, David": (4642, 5071),
    "Adams, Lynda": (2793, 3342),
    "McWilliams, Lawrence P.": (2092, 0),
    "Inactive ballots": (1241, 2355),
}
THRESHOLD = 4207
METHOD = "This data comes from the city clerk's certified ranked-choice tabulation, which exists only as a scanned page. The counts were verified with a second OCR pass. Round 1 counts each ballot's highest valid ranking; this results in a few votes' difference from the first-choice column printed in the same results. Inactive ballots (those with no valid ranking) are counted as invalid in round 1, while the additional inactive ballots in round 2 are exhausted."


def fetch() -> None:
    raw.fetch(SOURCE, REPORT, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    pdf = raw.cached(SOURCE, REPORT).path
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "page"
        subprocess.run(
            ["pdftoppm", "-r", "300", "-png", "-f", str(PAGE), "-l", str(PAGE), "-singlefile", str(pdf), str(image)],
            check=True,
        )
        width, height = struct.unpack(">II", image.with_suffix(".png").read_bytes()[16:24])
        tsv = subprocess.run(
            ["tesseract", str(image.with_suffix(".png")), "-", "tsv"], check=True, capture_output=True, text=True
        ).stdout
    words = []
    for line in tsv.splitlines()[1:]:
        cells = line.split("\t")
        if len(cells) == 12 and cells[11].strip():
            left, top, wide, tall = (int(cell) for cell in cells[6:10])
            words.append((cells[11].strip(), left, top, left + wide, top + tall))
    rows = {}
    for label, expected in TRANSCRIBED.items():
        first = label.split()[0]
        (anchor,) = [word for word in words if word[0] == first and word[1] < width * 0.15 and word[2] > height * 0.2]
        figures = sorted(
            (word for word in words if abs(word[2] - anchor[2]) < 15 and word[1] > width / 2 and word[0].isdigit()),
            key=lambda word: word[1],
        )
        if tuple(int(word[0]) for word in figures) != expected:
            raise ValueError(f"{label}: OCR read {[word[0] for word in figures]}, transcribed {expected}")
        rows[label] = [
            {"votes": int(text), "box": [x0 / width, y0 / height, x1 / width, y1 / height]}
            for text, x0, y0, x1, y1 in figures
        ]
    threshold = [word for word in words if word[0] == str(THRESHOLD) and word[1] > width / 2]
    if len(threshold) != 1 or "Morse," not in {word[0] for word in words}:
        raise ValueError("threshold or winner not found on the page")
    totals = {sum(cells[number] for cells in TRANSCRIBED.values()) for number in (0, 1)}
    final = [cells[1] for label, cells in TRANSCRIBED.items() if label != "Inactive ballots"]
    if len(totals) != 1 or THRESHOLD != sum(final) // 2 + 1 or max(final) < THRESHOLD:
        raise ValueError("rounds do not reconcile with each other or the threshold")
    (PARSED / "official.json").write_text(json.dumps(rows))


def emit() -> Path:
    rows = json.loads((PARSED / "official.json").read_text())
    day = date(2024, 11, 5)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Westbrook, Maine's first ranked-choice election, the 2024 mayoral race, from the city clerk's "
            "certified tabulation in the official results",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        town = cs.area("us.town", "2300582105")
        top = cs.unit("us.town", "2300582105")
        if {town, top} & cs.minted:
            raise ValueError("Westbrook must already exist")
        ((polity,),) = conn.execute("select polity_id from polity_territory where area_id = %s", [town]).fetchall()
        ((parent,),) = conn.execute(
            """select e.id from election e join name n on n.entity_id = e.id
               where e.date_from = %s and n.text = '2024 Maine general election' and e.polity_id = %s""",
            [day, cs.polity("US-ME")],
        ).fetchall()
        mayor = cs.key("us.office", "ME:2300582105:mayor")
        cs.add("office", id=mayor, polity_id=polity, kind="mayor")
        name(mayor, "Mayor of Westbrook")
        irv = cs.key("us.electoral_system", "westbrook-me:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        name(irv, "Westbrook ranked-choice voting")
        election = cs.key("us.election", f"{day}:ME:2300582105")
        cs.add(
            "election",
            id=election,
            polity_id=polity,
            parent_id=parent,
            kind="local",
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        name(election, "2024 Westbrook local election")
        cs.add("slug", path=f"us/me/westbrook/{day}-local", entity_id=election)
        contest = cs.key("us.contest", f"{day}:ME:2300582105:mayor")
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=mayor,
            body_id=None,
            district_area_id=town,
            magnitude=1,
            round_number=1,
            electoral_system_id=irv,
            default_result_kind="counted",
            term="full",
        )
        name(contest, "2024 Westbrook mayoral election")
        cs.add("slug", path=f"us/me/westbrook/{day}-local/mayor", entity_id=contest)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=REPORT,
            license="Public record",
        )
        pdf = raw.cached(SOURCE, REPORT)
        artifact = cs.artifact(pdf, source)
        image = PAGES / f"{pdf.sha256}-{PAGE}.png"
        if not image.exists():
            PAGES.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["pdftoppm", "-r", "100", "-png", "-f", str(PAGE), "-l", str(PAGE), "-singlefile"]
                + [str(pdf.path), str(image.with_suffix(""))],
                check=True,
            )
        width, height = struct.unpack(">II", image.read_bytes()[16:24])
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

        people: dict[tuple[str, str], set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id where e.polity_id = %s""",
            [polity],
        ).fetchall():
            words = text.casefold().replace(",", "").split()
            people.setdefault((words[0], words[-1]), set()).add(person)

        result = cs.key(f"{SOURCE}.result_set", f"{day}:mayor")
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
        cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
        inactive = rows.pop("Inactive ballots")
        ballots = sum(cells[0]["votes"] for cells in rows.values()) + inactive[0]["votes"]
        values: list[tuple[UUID | None, int, int, int, list[float] | None]] = [
            (None, 3, 0, ballots, None),
            (None, 5, 0, inactive[0]["votes"], inactive[0]["box"]),
            (None, 4, 1, ballots - inactive[0]["votes"], None),
            (None, 21, 1, 0, None),
            (None, 21, 2, inactive[1]["votes"] - inactive[0]["votes"], None),
        ]
        final = {label: cells[1]["votes"] for label, cells in rows.items()}
        winner = max(final, key=lambda label: final[label])
        for label, cells in rows.items():
            last, first = label.split(", ")
            text = f"{first} {last}"
            words = text.casefold().split()
            matches = people.get((words[0], words[-1]), set())
            if len(matches) == 1:
                (person,) = matches
            else:
                person = cs.key(f"{SOURCE}.person", f"{words[0]}-{words[-1]}")
                cs.add("person", id=person)
                name(person, text)
            option = cs.key("us.option", f"{contest}:{person}")
            cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=text)
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=text,
                member_of_party_id=None,
                is_independent=False,
                is_incumbent=None,
            )
            for number, cell in enumerate(cells, 1):
                if number == 1 or cells[number - 2]["votes"]:
                    values.append((option, 1, number, cell["votes"], cell["box"]))
            for outcome in ("won", "elected") if label == winner else ("lost" if final[label] else "eliminated",):
                cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        for option, measure, number, votes, box in values:
            cs.add(
                "result_value",
                result_set_id=result,
                unit_id=top,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=number,
                flags=8 if box is not None else 1,
                value=votes,
            )
            if box is not None:
                x0, y0, x1, y1 = box
                cs.add(
                    "value_locator",
                    result_set_id=result,
                    unit_id=top,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    page_id=page,
                    source_id=None,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )
        return cs.write()
