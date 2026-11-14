import csv
import io
import subprocess
import tempfile
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.pr_cee_legislature"
VERSION = "1"
TSV = Path(__file__).with_suffix(".tsv")
PAGES = DATA / "pages" / SOURCE
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Puerto Rico State Elections Commission"
TITLE = "Informe Estadístico, Elecciones Generales: votes for the Legislative Assembly"
SITE = "https://ww2.ceepur.org/sites/ComisionEE/es-pr/Informe%20Estadstico/"
REPORTS = {
    1976: date(1976, 11, 2),
    1980: date(1980, 11, 4),
    1984: date(1984, 11, 6),
    1988: date(1988, 11, 8),
}
PARTIES = {
    "PPD": "PR:popular-democratic-party",
    "PNP": "PR:new-progressive-party",
    "PIP": "PR:puerto-rican-independence-party",
    "PSP": "PR:puerto-rican-socialist-party",
    "PRP": "PR:puerto-rico-renewal",
    "IND": None,
}
SENATORS = {"at-large": 11, **{str(n): 2 for n in range(1, 9)}}
REPRESENTATIVES = {"at-large": 11, **{str(n): 1 for n in range(1, 41)}}
PARTICLES = {"de", "del", "la", "las", "los", "y"}


def url(year: int) -> str:
    return f"{SITE}ESTADISTICAS%20DE%20LAS%20ELECCIONES%20{year}.pdf"


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn").strip()


def display(name: str) -> str:
    if name != name.upper():
        return name
    words = [word.capitalize() for word in name.split()]
    return " ".join(word.lower() if index and word.lower() in PARTICLES else word for index, word in enumerate(words))


def rows() -> list[dict[str, str]]:
    with TSV.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file, delimiter="\t"))


def fetch() -> None:
    for year in REPORTS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def parse() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    located = []
    lines = rows()
    for year in REPORTS:
        artifact = raw.cached(SOURCE, url(year))
        for number in sorted({int(row["page"]) for row in lines if int(row["year"]) == year}):
            image = PAGES / f"{artifact.sha256}-{number}.png"
            if not image.exists():
                with tempfile.TemporaryDirectory() as folder:
                    stem = Path(folder) / "page"
                    subprocess.run(
                        ["pdftoppm", "-r", "300", "-gray", "-png", "-singlefile", "-f", str(number), "-l", str(number)]
                        + [str(artifact.path), str(stem)],
                        check=True,
                    )
                    stem.with_suffix(".png").rename(image)
            words = pl.read_csv(
                io.StringIO(
                    subprocess.run(
                        ["tesseract", str(image), "-", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
                    ).stdout
                ),
                separator="\t",
                quote_char=None,
                infer_schema=False,
            ).filter(pl.col("text").is_not_null())
            with Image.open(image) as opened:
                width, height = opened.size
            for row in lines:
                if int(row["year"]) != year or int(row["page"]) != number:
                    continue
                matches = words.filter(
                    pl.col("text").str.replace_all(r"[^\d]", "") == row["votes"].replace(".", ""),
                    pl.col("text").str.contains(r"^[\d,.]+$"),
                )
                if matches.is_empty():
                    continue
                key = "top" if row["district"] == "at-large" else "left"
                best = max(matches.iter_rows(named=True), key=lambda word: int(word[key]))
                located.append(
                    {
                        "year": year,
                        "chamber": row["chamber"],
                        "district": row["district"],
                        "candidate": row["candidate"],
                        "page": number,
                        "width": width,
                        "height": height,
                        "x0": int(best["left"]),
                        "y0": int(best["top"]),
                        "x1": int(best["left"]) + int(best["width"]),
                        "y1": int(best["top"]) + int(best["height"]),
                    }
                )
    pl.DataFrame(located).write_parquet(PARSED / "locators.parquet")
    print(f"{len(located)} of {len(lines)} values located")


def emit() -> Path:
    lines = rows()
    boxes = {
        (row["year"], row["chamber"], row["district"], row["candidate"]): row
        for row in pl.read_parquet(PARSED / "locators.parquet").iter_rows(named=True)
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico Senate and House of Representatives general elections 1976-1988 by district and at large, read from the State Elections Commission's statistical reports",
            polity="US-PR",
        )
        island_area = cs.area("us.state", "72")
        island_unit = cs.unit("us.state", "72")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://ww2.ceepur.org/Home/Estadisticas",
            license="Public record",
        )
        artifacts = {year: cs.artifact(raw.cached(SOURCE, url(year)), source) for year in REPORTS}
        pages: dict[tuple[int, int], UUID] = {}
        for year in REPORTS:
            sha = raw.cached(SOURCE, url(year)).sha256
            for number in sorted({int(row["page"]) for row in lines if int(row["year"]) == year}):
                image = PAGES / f"{sha}-{number}.png"
                pages[year, number] = cs.page(artifacts[year], number)
                with Image.open(image) as opened:
                    cs.add(
                        "source_page",
                        id=pages[year, number],
                        artifact_id=artifacts[year],
                        page_number=number,
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=opened.width,
                        height=opened.height,
                    )
        systems = {
            "fptp": cs.key("us.electoral_system", "fptp"),
            "block": cs.key("us.electoral_system", "block"),
            "sntv": cs.key("us.electoral_system", "PR:at-large"),
        }
        bodies = {
            "senate": (cs.key("us.body", "PR:senate"), "Senate of Puerto Rico", SENATORS),
            "house": (cs.key("us.body", "PR:house"), "Puerto Rico House of Representatives", REPRESENTATIVES),
        }
        persons: dict[str, UUID] = {}
        contests: dict[tuple[int, str, str], list[dict[str, str]]] = {}
        for row in lines:
            contests.setdefault((int(row["year"]), row["chamber"], row["district"]), []).append(row)
        elected_total: dict[tuple[str, int], int] = {}
        for (year, chamber, where), entries in sorted(contests.items()):
            body, body_name, magnitudes = bodies[chamber]
            day = REPORTS[year]
            at_large = where == "at-large"
            if at_large:
                area, unit, shown = island_area, island_unit, "at large"
            else:
                scheme = "us.sldu" if chamber == "senate" else "us.sldl"
                area, unit, shown = cs.area(scheme, f"PR:{where}"), cs.unit(scheme, f"PR:{where}"), f"District {where}"
            contest_key = f"{day}:PR:{chamber}:{where}:g"
            contest = cs.key("us.contest", contest_key)
            regular = sum(1 for row in entries if row["elected"] == "1")
            added = sum(1 for row in entries if row["elected"] == "+")
            magnitude = magnitudes[where]
            if regular != magnitude:
                raise ValueError(f"{contest_key}: {regular} elected")
            elected_total[(chamber, year)] = elected_total.get((chamber, year), 0) + regular + added
            election = cs.key("us.election", f"{day}:PR")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=magnitude,
                term="full",
                round_number=1,
                previous_round_id=None,
                electoral_system_id=systems["sntv" if at_large else "block" if chamber == "senate" else "fptp"],
                default_result_kind="counted",
            )
            if contest in cs.minted:
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{year} {body_name} election, {shown}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/pr/{day}-general/state-{chamber}-{where}", entity_id=contest)
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
            page_list = sorted({int(row["page"]) for row in entries})
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=" ".join(
                    text
                    for text, shown_if in (
                        (
                            f"Votes read from page{'s' if len(page_list) > 1 else ''} {', '.join(map(str, page_list))} of the State Elections Commission's Informe Estadístico for the {year} general election, a scanned table; each total was read twice and checked against the precinct columns or totals row printed with it. Winners are those the report lists as elected.",
                            True,
                        ),
                        (
                            f"Each voter marks one at-large candidate, and the {magnitude} with the most votes are elected; the report credits ballots cast for a party's insignia alone to that party's at-large candidates precinct by precinct.",
                            at_large,
                        ),
                        (
                            f"{added} more {'candidate was' if added == 1 else 'candidates were'} declared elected to additional seats, which the Constitution (Article III, Section 7) gives minority parties when one party wins more than two thirds of a chamber.",
                            added,
                        ),
                    )
                    if shown_if
                ),
            )
            for row in entries:
                label = display(row["candidate"])
                if row["candidate"] == "Otros":
                    option = cs.key("us.option", f"{contest_key}:scattering")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in",
                        party_id=None,
                        ballot_label="Scattered write-ins",
                    )
                else:
                    token = slug(fold(label))
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    party_key = PARTIES[row["party"]]
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party_key) if party_key else None,
                        ballot_label=label,
                    )
                    if token not in persons:
                        persons[token] = cs.key("us.pr_legislature.person", token)
                        if persons[token] in cs.minted:
                            cs.add("person", id=persons[token])
                            cs.add(
                                "name",
                                entity_id=persons[token],
                                text=label,
                                lang="en",
                                kind="official",
                                is_preferred=True,
                            )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                        person_id=persons[token],
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=party_key is None,
                    )
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                box = boxes.get((row["year"], chamber, where, row["candidate"]))
                cs.add("result_value", **fact, flags=house_books.DIGITIZED if box else 0, value=Decimal(row["votes"]))
                if box:
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=pages[year, box["page"]],
                        source_id=None,
                        x0=box["x0"] / box["width"],
                        y0=box["y0"] / box["height"],
                        x1=box["x1"] / box["width"],
                        y1=box["y1"] / box["height"],
                    )
                if row["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        for (chamber, year), count in sorted(elected_total.items()):
            if count < (27 if chamber == "senate" else 51):
                raise ValueError(f"{chamber} {year}: {count} elected")
            print(chamber, year, count)
        return cs.write()
