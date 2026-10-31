import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.ks_totals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Kansas Secretary of State"
TITLE = "{year} General Election Official Vote Totals"
LANDING = "https://sos.ks.gov/elections/election-results.html"
LICENSE = "Public record"
FILES = {
    date(2006, 11, 7): "https://sos.ks.gov/elections/06elec/2006GeneralElectionOfficialResults.pdf",
    date(2010, 11, 2): "https://sos.ks.gov/elections/10elec/2010_General_Election_Results.pdf",
}
OFFICES = {
    "Secretary of State": ("secretary-of-state", "Secretary of State"),
    "Attorney General": ("attorney-general", "Attorney General"),
    "State Treasurer": ("treasurer", "Treasurer"),
    "Commissioner of Insurance": ("commissioner-of-insurance", "Commissioner of Insurance"),
}
PARTIES = {"D": "democratic-party", "R": "republican-party", "L": "libertarian-party"}
SCANNED = {
    ("Secretary of State", "D", "Chris Biggs"): 308641,
    ("Secretary of State", "R", "Kris Kobach"): 489640,
    ("Secretary of State", "L", "Phillip Horatio Lucas"): 17336,
    ("Secretary of State", "F", "Derek Langseth"): 13896,
    ("Attorney General", "D", "Steve Six"): 349340,
    ("Attorney General", "R", "Derek Schmidt"): 458497,
    ("Attorney General", "L", "Dennis Hawver"): 26867,
    ("State Treasurer", "D", "Dennis McKinney"): 341324,
    ("State Treasurer", "R", "Ron Estes"): 481704,
    ("Commissioner of Insurance", "R", "Sandy Praeger"): 674573,
    ("Commissioner of Insurance", "", "David J Powell"): 2570,
}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    path = raw.cached(SOURCE, FILES[date(2006, 11, 7)]).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    office = None
    for line in text.splitlines():
        if line.strip() in OFFICES and not line.startswith(" " * 5):
            office = line.strip()
            continue
        if office is None:
            continue
        match = re.match(r"^\s{4,}(?:([A-Z])-)?(\S.*?)\s{2,}([\d,]+)\s+\.?[\d.]+ ?%\s*$", line)
        if match:
            rows.append(
                {
                    "day": date(2006, 11, 7),
                    "office": office,
                    "party": match[1] or "",
                    "name": re.sub(r"\s+", " ", match[2]).strip(),
                    "votes": int(match[3].replace(",", "")),
                    "box": None,
                }
            )
        elif line.strip() and not line.startswith(" " * 5):
            office = None
    image = PAGES / "ks2010-1.png"
    PAGES.mkdir(parents=True, exist_ok=True)
    path = raw.cached(SOURCE, FILES[date(2010, 11, 2)]).path
    subprocess.run(
        ["pdftoppm", "-r", "200", "-f", "1", "-l", "1", "-png", "-singlefile", str(path), str(image.with_suffix(""))],
        check=True,
    )
    tsv = subprocess.run(
        ["tesseract", str(image), "-", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    words = [line.split("\t") for line in tsv[1:] if line.count("\t") == 11 and line.split("\t")[11].strip()]
    for (office, party, name), votes in SCANNED.items():
        boxes = [
            (int(w[6]), int(w[7]), int(w[6]) + int(w[8]), int(w[7]) + int(w[9]))
            for w in words
            if re.sub(r"\D", "", w[11]) == str(votes) and "," in w[11]
        ]
        if len(boxes) != 1:
            raise ValueError(f"{office} {name}: {len(boxes)} boxes for {votes}")
        rows.append(
            {
                "day": date(2010, 11, 2),
                "office": office,
                "party": party,
                "name": name,
                "votes": votes,
                "box": list(boxes[0]),
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema_overrides={"box": pl.List(pl.Int64)})
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.drop("box"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's 2006 and 2010 secretary of state, attorney general, treasurer, and insurance "
            "commissioner elections by state from the Secretary of State's official vote totals",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [
                word
                for word in re.sub(r"\([^)]*\)|\"[^\"]*\"", " ", text).replace(",", " ").split()
                if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}
            ]
            return plain(words[-1])

        sources: dict[date, UUID] = {}
        pages: dict[date, tuple[UUID, int, int]] = {}
        for day, url in FILES.items():
            title = TITLE.format(year=day.year)
            sources[day] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[day],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=LANDING,
                license=LICENSE,
            )
            artifact = cs.artifact(raw.cached(SOURCE, url), sources[day])
            if day.year == 2010:
                image = PAGES / "ks2010-1.png"
                with Image.open(image) as rendered:
                    width, height = rendered.size
                page = cs.page(artifact, 1)
                pages[day] = (page, width, height)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=1,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        ((area, state_code),) = conn.execute(
            """select u.area_id, u.code from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-KS'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        contests = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^(2006|2010)-.*:KS:'"
            )
        }
        people: dict[str, list[tuple[UUID, int]]] = {}
        for person, text, year in conn.execute(
            """select distinct cd.person_id, n.text, extract(year from e.date_from)::int from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id
               join name n on n.entity_id = cd.person_id and n.is_preferred
               where c.district_area_id = %s""",
            [area],
        ).fetchall():
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", []).append((person, year))
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must already exist")
        made: set[UUID] = set()
        for (day, office), lines in frame.sort("day", "office").group_by("day", "office", maintain_order=True):
            base, title = OFFICES[str(office)]
            key = f"{day}:KS:{base}"
            if key in contests:
                print(key, "is already in the archive")
                continue
            election = elections[f"{day}:KS"]
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("us.office", f"{base}:KS"),
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Kansas {title} election")
            cs.add("slug", path=f"us/ks/{day}-general/{base}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[day],
                is_default=True,
                completeness=1,
            )
            totals: dict[UUID, int] = {}
            for party, candidate, votes, box in lines.select("party", "name", "votes", "box").iter_rows():
                probe = f"{plain(candidate.split()[0])}|{surname(candidate)}"
                found = {person for person, seen in people.get(probe, []) if abs(seen - day.year) <= 16}
                person = next(iter(found)) if len(found) == 1 else cs.key(f"{SOURCE}.person", f"KS:{slug(candidate)}")
                if person in cs.minted and person not in made:
                    made.add(person)
                    cs.add("person", id=person)
                    name(person, candidate)
                    people.setdefault(probe, []).append((person, day.year))
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if party else "write_in",
                    party_id=parties.get(party),
                    ballot_label=candidate,
                )
                if party:
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=party not in PARTIES,
                    )
                fact = {
                    "result_set_id": result_set,
                    "unit_id": cs.unit("us.state", state_code),
                    "option_id": option,
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED if box is not None else 0, value=votes)
                if box is not None:
                    page, width, height = pages[day]
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
                totals[option] = votes
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=max(totals, key=totals.__getitem__),
                kind="elected",
                seats=1,
            )
        return cs.write()
