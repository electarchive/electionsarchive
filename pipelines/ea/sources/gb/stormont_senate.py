import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.stormont_senate"
VERSION = "1"
PAGES = DATA / "pages" / SOURCE
NOTICES = Path(__file__).with_suffix(".tsv")
GAZETTE = "https://www.thegazette.co.uk/Belfast/issue/{}/page/{}/data.pdf"
HANSARD = "https://archive.org/download/civgraph-ni-hansard-1921-1945/data/books/markdown/{}.md"
VOLUMES = {"1921_Senate": "Senate, volume 1 (1921)", "1922_Senate": "Senate, volume 2 (1922)"}
DEBATES = {
    "1921_House_of_Commons": "House of Commons, volume 1 (1921)",
    "1925_House_of_Commons": "House of Commons, volume 6 (1925)",
}
FIRST = (date(1921, 6, 11), date(1921, 6, 20))
CONTESTED = date(1925, 5, 1)
OPENED = date(1921, 6, 7)
DISSOLVED = date(1973, 7, 18)
METHODOLOGY = (
    "The 24 elected members of the Senate of Northern Ireland were chosen by the members of the House of Commons of "
    "Northern Ireland by single transferable vote, 12 every four years for eight-year terms, with casual vacancies "
    "filled the same way; the Lord Mayor of Belfast and the Mayor of Londonderry sat ex officio. The Clerk of the "
    "Crown for Northern Ireland gazetted the names of those elected, and the Speaker reported them to the Senate. "
    "Neither printed a count, the candidates who lost, or whether a poll was held, so these contests record the "
    "senators elected without votes. Each election is dated by the Clerk of the Crown's notice of the return."
)
FIRST_NOTE = (
    "Nominations closed at noon on 11 June 1921, and the Speaker announced that any poll would close on 16 June. The "
    "Senate first sat on 20 June 1921, when these 24 elected senators took the oath. On 30 April 1925 the Speaker "
    "told the Commons that the election of two senators then pending was the first contested election of senators, "
    "so this election, and the casual vacancies filled before it, were uncontested."
)
DEVOTO = (
    "In the Commons debate of 2 September 1925, the Attorney-General said the quota was 293 and that a Nationalist "
    "candidate, Mr. Devoto, was not elected; three Nationalist members' postal votes reached the Speaker after the "
    "poll closed at 10 a.m. on 14 July and were not counted. The losing candidates' names and votes are not printed, "
    "so only those elected are recorded."
)


def fetch() -> None:
    notices = pl.read_csv(NOTICES, separator="\t", infer_schema=False)
    for file in notices["file"].unique(maintain_order=True):
        if file in VOLUMES:
            raw.fetch(SOURCE, HANSARD.format(file), suffix=".md")
        else:
            raw.fetch(SOURCE, GAZETTE.format(*file.split("_")[1:3]), suffix=".pdf")
    for file in DEBATES:
        raw.fetch(SOURCE, HANSARD.format(file), suffix=".md")


def image(path: Path, sha256: str) -> tuple[str, int, int, float, float]:
    PAGES.mkdir(parents=True, exist_ok=True)
    png = PAGES / f"{sha256}.png"
    if not png.exists():
        subprocess.run(
            ["pdftoppm", "-r", "150", "-gray", "-png", "-singlefile", str(path), str(png.with_suffix(""))], check=True
        )
    out = subprocess.run(
        ["magick", "identify", "-format", "%w %h", str(png)], check=True, capture_output=True, text=True
    )
    width, height = map(int, out.stdout.split())
    size = re.search(
        r"Page size:\s+([\d.]+) x ([\d.]+)",
        subprocess.run(["pdfinfo", str(path)], check=True, capture_output=True, text=True).stdout,
    )
    assert size, path
    return f"pages/{SOURCE}/{png.name}", width, height, float(size.group(1)), float(size.group(2))


def emit() -> Path:
    notices = pl.read_csv(NOTICES, separator="\t", infer_schema=False).filter(pl.col("kind") != "none")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Senate of Northern Ireland elections 1921 to 1969, general and casual vacancy, with the senators "
            "elected from the Belfast Gazette's notices and the Senate's first roll",
            polity="GB-NIR",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        nir = cs.polity("GB-NIR")
        stv = cs.key("gb.electoral_system", "stv")
        if stv in cs.minted:
            raise ValueError("publish the stv electoral system first")
        body = cs.key("gb.body", "stormont-senate")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=nir, kind="upper_house")
            name(body, "Senate of Northern Ireland")
            cs.add(
                "body_size",
                body_id=body,
                seats=26,
                valid_from=OPENED,
                valid_from_precision="day",
                valid_to=DISSOLVED,
                valid_to_precision="day",
            )
        gazette = cs.source("The Gazette", "The Belfast Gazette: Senators elected")
        cs.add(
            "source",
            id=gazette,
            kind="official_body",
            publisher="The Gazette (The Stationery Office)",
            title="The Belfast Gazette: Senators elected",
            url="https://www.thegazette.co.uk/Belfast",
            attribution="Notices of the Office of the Clerk of the Crown for Northern Ireland in The Belfast Gazette, "
            "read from The Gazette's page PDFs.",
        )
        hansard = cs.source("Parliament of Northern Ireland", "Parliamentary Debates (Official Report), 1921 and 1925")
        cs.add(
            "source",
            id=hansard,
            kind="official_body",
            publisher="Parliament of Northern Ireland",
            title="Parliamentary Debates (Official Report), 1921 and 1925",
            url="https://archive.org/details/civgraph-ni-hansard-1921-1945",
            attribution="Parliamentary Debates of the Senate and House of Commons of Northern Ireland, read from the "
            "OCR text of the 1921 and 1925 volumes on the Internet Archive.",
            notes="The OCR text was made from scanned volumes held by the University of California and is not a "
            "corrected transcription; each name here was checked against the printed roll and the Gazette's later "
            "notices.",
        )
        artifacts: dict[str, UUID] = {}
        pages: dict[str, tuple[UUID, float, float]] = {}
        for file in notices["file"].unique(maintain_order=True):
            if file in VOLUMES:
                artifacts[file] = cs.artifact(raw.cached(SOURCE, HANSARD.format(file)), hansard)
                continue
            artifact = raw.cached(SOURCE, GAZETTE.format(*file.split("_")[1:3]))
            artifacts[file] = cs.artifact(artifact, gazette)
            key, width, height, points_wide, points_high = image(artifact.path, artifact.sha256)
            page = cs.page(artifacts[file], 1)
            if page in cs.minted:
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifacts[file],
                    page_number=1,
                    image_key=key,
                    width=width,
                    height=height,
                )
            pages[file] = (page, points_wide, points_high)
        for file, volume in DEBATES.items():
            debates = cs.artifact(raw.cached(SOURCE, HANSARD.format(file)), hansard)
            cs.add("note", entity_id=debates, kind="context", body_md=f"{volume}.")
        nation = cs.area("gb.country", "NIR", on=CONTESTED)
        island = cs.area("gb.country", "IRL", on=OPENED)
        if nation in cs.minted or island in cs.minted:
            raise ValueError("no gb.country NIR or IRL")

        persons: dict[str, UUID] = {}
        for (day_text, kind), group in notices.group_by("notice_date", "kind", maintain_order=True):
            day = date.fromisoformat(day_text)
            general = kind == "general"
            first = day < date(1921, 7, 1)
            assert not (first and not general)
            election = cs.key(f"{SOURCE}.election", f"{day}:{kind}")
            sworn = group["file"][0] in VOLUMES and not first
            start, end = FIRST if first else (date(day.year, 1, 1),) * 2 if sworn else (day, day)
            cs.add(
                "election",
                id=election,
                polity_id=nir,
                kind="indirect",
                date_from=start,
                date_to=end,
                date_precision="year" if sworn else "day",
                status="held",
            )
            spoken = str(day.year) if sworn else f"{day:%B} {day.day}, {day.year}"
            stamp = f"{day.year}-{slug(group['replaces'][0])}" if sworn else str(start)
            path = f"gb/northern-ireland/{stamp}-senate-{'election' if general else 'casual-vacancy'}"
            name(
                election,
                f"{start.year} Northern Ireland Senate election"
                if general
                else f"Northern Ireland Senate casual {'vacancy' if group.height == 1 else 'vacancies'}, {spoken}",
            )
            cs.add("slug", path=path, entity_id=election)
            cs.add("note", entity_id=election, kind="methodology", body_md=METHODOLOGY)
            tacit = day < CONTESTED
            contest = cs.key(f"{SOURCE}.contest", f"{day}:{kind}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=island if first else nation,
                magnitude=group.height,
                round_number=1,
                electoral_system_id=stv,
                default_result_kind="tacit" if tacit else "counted",
            )
            name(
                contest,
                f"Senate of Northern Ireland, {start.year}" if general else f"Senate of Northern Ireland, {spoken}",
            )
            cs.add("slug", path=f"{path}/senate", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{kind}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=hansard if first else gazette,
                is_default=True,
                completeness=None if tacit else 0,
            )
            if first:
                cs.add("note", entity_id=result_set, kind="context", body_md=FIRST_NOTE)
                for volume, sitting in (
                    (DEBATES["1921_House_of_Commons"], "7 June 1921"),
                    (DEBATES["1925_House_of_Commons"], "30 April 1925"),
                ):
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=hansard,
                        locator={"volume": volume, "sitting": sitting},
                    )
            if sworn:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"No notice of this return was found in The Belfast Gazette. The Senate's journal records "
                    f"the new senator taking the oath on {day.day} {day:%B} {day.year}, so the election was held in "
                    f"{day.year} on or before that day.",
                )
            if general and day.year == 1925:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=DEVOTO)
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=hansard,
                    locator={"volume": DEBATES["1925_House_of_Commons"], "sitting": "2 September 1925"},
                )
            for (file,), rows in group.group_by("file", maintain_order=True):
                if file in VOLUMES:
                    locator: dict[str, object] = {
                        "volume": VOLUMES[file],
                        "section": "Alphabetical list of members"
                        if first
                        else f"New member sworn, {day.day} {day:%B} {day.year}",
                    }
                    cs.add("citation", entity_id=result_set, source_id=hansard, locator=locator)
                    continue
                page, points_wide, points_high = pages[file]
                x0, y0, x1, y1 = (float(value) for value in rows["bbox"][0].split(","))
                locator = {
                    "page_id": str(page),
                    "bbox": [
                        round(x0 / points_wide, 6),
                        round(y0 / points_high, 6),
                        round(x1 / points_wide, 6),
                        round(y1 / points_high, 6),
                    ],
                    "issue": int(file.split("_")[1]),
                    "page": int(file.split("_")[2]),
                }
                cs.add("citation", entity_id=result_set, source_id=gazette, locator=locator)
            for row in group.iter_rows(named=True):
                option_key = f"{day}:{kind}:{row['seq']}"
                option = cs.key(f"{SOURCE}.option", option_key)
                identity = row["name"]
                if identity not in persons:
                    persons[identity] = cs.key(f"{SOURCE}.person", identity)
                    if persons[identity] in cs.minted:
                        cs.add("person", id=persons[identity])
                        name(persons[identity], identity)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_label=row["style"],
                )
                if row["replaces"]:
                    cs.add(
                        "note",
                        entity_id=option,
                        kind="context",
                        body_md=f"Elected in the room of {row['replaces']}, {row['cause']}.",
                    )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", option_key),
                    person_id=persons[identity],
                    option_id=option,
                    role="head",
                    ballot_name=row["style"],
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
