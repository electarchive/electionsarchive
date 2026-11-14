import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.fl_ppp_history"
VERSION = "1"
TABLE = Path(__file__).with_suffix(".tsv")
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Florida Secretary of State"
ITEMS = {
    1932: "TabulationofofficialvoteFloridaprimaryelections1932",
    1936: "TabulationofofficialvoteFloridaprimaryelections1936",
    1952: "TabulationofofficialvoteFloridaprimaryelections19521",
    1956: "TabulationofofficialvoteFloridaprimaryelections1956",
    1960: "TabulationofofficialvoteFloridaprimaryelections1960",
    1964: "TabulationofofficialvoteFloridaprimaryelections1964",
    1968: "TabulationofofficialvoteFloridaprimaryelections1968",
}
PARTIES = {"democratic": ("Democratic", "democratic-party"), "republican": ("Republican", "republican-party")}
BOXES = {
    (1932, 3): (0.364, 0.234, 0.586, 0.915),
    (1936, 3): (0.276, 0.262, 0.448, 0.924),
    (1952, 4): (0.338, 0.215, 0.551, 0.851),
    (1956, 12): (0.107, 0.247, 0.281, 0.896),
    (1956, 13): (0.106, 0.247, 0.277, 0.896),
    (1956, 16): (0.107, 0.247, 0.281, 0.895),
    (1956, 17): (0.106, 0.246, 0.278, 0.900),
    (1960, 15): (0.085, 0.243, 0.251, 0.883),
    (1960, 18): (0.096, 0.243, 0.263, 0.883),
    (1964, 16): (0.085, 0.190, 0.456, 0.860),
    (1964, 20): (0.085, 0.188, 0.474, 0.847),
    (1964, 22): (0.103, 0.202, 0.487, 0.863),
    (1968, 31): (0.113, 0.144, 0.475, 0.942),
    (1968, 33): (0.113, 0.144, 0.475, 0.941),
    (1968, 35): (0.113, 0.145, 0.475, 0.944),
    (1968, 37): (0.114, 0.144, 0.480, 0.942),
}
SLATES = (
    "Florida voters chose slates of national convention delegates pledged to or preferring a candidate (or with no "
    "preference); the vote shown for each candidate is the vote for the slate of delegates at large."
)
ZERO = "A county cell printed as a dash or a dotted line is stored as zero."
NOTES = {
    "1932-06-07:democratic": "The scattering votes are the write-ins listed in the volume's footnote: Collier 1, "
    "Orange 26, Osceola 3, and Putnam 2.",
    "1956-05-29:democratic": f"{SLATES} The printed state-at-large total for Kefauver is 216,549; the county figures "
    "and the eight printed district totals both sum to 216,541.",
    "1956-05-29:republican": f"{SLATES} {ZERO}",
    "1960-05-24:democratic": f"{SLATES} Calhoun prints 1,198 in the state-at-large column and 1,193 in the Third "
    "District column; the printed state-at-large total agrees with 1,198.",
    "1960-05-24:republican": f"{SLATES} {ZERO}",
    "1964-05-26:democratic": SLATES,
    "1964-05-26:republican": f"{SLATES} {ZERO}",
    "1968-05-28:democratic": SLATES,
    "1968-05-28:republican": f"{SLATES} {ZERO}",
}


def url(year: int) -> str:
    return f"https://archive.org/download/{ITEMS[year]}/{ITEMS[year]}.pdf"


def title(year: int) -> str:
    return f"Tabulation of Official Vote, Florida Primary Elections, {year}"


def fetch() -> None:
    for year in ITEMS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def kind(option: str) -> str:
    return {"No preference": "uncommitted", "Scattering": "write_in"}.get(option, "candidate")


def emit() -> Path:
    frame = pl.read_csv(TABLE, separator="\t", schema_overrides={"county": pl.String, "votes": pl.Int64}).with_columns(
        pl.col("day").str.to_date()
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida presidential preference primaries of 1932, 1936, 1952, 1956, 1960, 1964, and 1968 by "
            "county from the Secretary of State's Tabulation of Official Vote",
            polity="US",
        )

        def name(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '12%%'"
            ).fetchall()
        )
        if len(counties) != 67:
            raise ValueError("Florida needs 67 counties")
        florida = cs.polity("US-FL")
        ((state,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-FL'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {key: cs.key("us.party", value) for key, (_, value) in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], UUID] = {}
        PAGES.mkdir(parents=True, exist_ok=True)
        for year, item in ITEMS.items():
            source = sources[year] = cs.source(PUBLISHER, title(year))
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title(year),
                url=f"https://archive.org/details/{item}",
                license="Public domain",
            )
            pdf = raw.cached(SOURCE, url(year))
            artifact = cs.artifact(pdf, source)
            for page_year, number in BOXES:
                if page_year != year:
                    continue
                image = PAGES / f"{pdf.sha256}-{number}.png"
                if not image.exists():
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(pdf.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = Image.open(image).size
                page = cs.page(artifact, number)
                pages[(year, number)] = page
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        made: set[UUID] = set()
        for (day, party), lines in frame.group_by("day", "party", maintain_order=True):
            year = day.year
            label, _ = PARTIES[party]
            key = f"{day}:FL:president:{party}"
            election = cs.key("us.election", f"{day}:FL:primary")
            if election not in made:
                made.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=florida,
                    kind="primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} Florida presidential preference primary")
                cs.add("slug", path=f"us/fl/{day}-primary", entity_id=election)
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("us.office", "president"),
                district_area_id=state,
                term="full",
                magnitude=1,
                round_number=1,
                party_scope_id=parties[party],
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{year} Florida {label} presidential preference primary")
            cs.add("slug", path=f"us/fl/{day}-primary/president-{party}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=1,
            )
            note = NOTES.get(f"{day}:{party}")
            if note:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=note)
            for option_text, number, votes in (
                lines.group_by("option", "page", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                option = cs.key("us.option", f"{key}:{slug(option_text)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind=kind(option_text),
                    party_id=parties[party] if kind(option_text) == "candidate" else None,
                    ballot_label=option_text,
                )
                if kind(option_text) == "candidate":
                    person = cs.key(f"{SOURCE}.person", f"FL:{year}:{slug(option_text)}")
                    cs.add("person", id=person)
                    name(person, option_text)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=option_text,
                        is_independent=False,
                    )
                x0, y0, x1, y1 = BOXES[(year, number)]
                for county, value in (
                    lines.filter(pl.col("option") == option_text).select("county", "votes").iter_rows()
                ):
                    cell = {
                        "result_set_id": result_set,
                        "unit_id": counties[county],
                        "option_id": option,
                        "channel": 0,
                        "measure": 1,
                        "count_round": 0,
                    }
                    cs.add("result_value", **cell, flags=0, value=value)
                    if option_text != "Scattering":
                        cs.add("value_locator", **cell, page_id=pages[(year, number)], x0=x0, y0=y0, x1=x1, y1=y1)
            leader = max(lines.group_by("option").agg(pl.col("votes").sum()).iter_rows(), key=lambda row: row[1])[0]
            if kind(leader) == "candidate":
                winner = cs.key("us.option", f"{key}:{slug(leader)}")
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="won", seats=None)
        return cs.write()
