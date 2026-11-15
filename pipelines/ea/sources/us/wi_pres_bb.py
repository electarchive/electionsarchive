import json
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import scans
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.sources.us.nh_pres_manual import FIRST
from ea.store import DATA, connect

SOURCE = "us.wi_pres_bb"
VERSION = "1"
VOLUME = "wi-bb-pres"
PUBLISHER = "Wisconsin Legislative Reference Bureau"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PARTIES = {"dem": ("Democratic", "democratic-party"), "rep": ("Republican", "republican-party")}
BOOKS = {
    1952: ("The Wisconsin Blue Book 1954", "https://search.library.wisc.edu/digital/AN6Q3X3SXGLMTH8Z"),
    1956: ("The Wisconsin Blue Book 1958", "https://search.library.wisc.edu/digital/A2BH4FP76N6HMK8V"),
    1960: ("The Wisconsin Blue Book 1962", "https://search.library.wisc.edu/digital/A5BZZBS2FDP3LV8O"),
    1964: ("The Wisconsin Blue Book 1966", "https://search.library.wisc.edu/digital/ANICGVB65P4ZNO8M"),
    1968: ("The Wisconsin Blue Book 1970", "https://search.library.wisc.edu/digital/A63MVAJVDHMHFW9E"),
    1972: ("The Wisconsin Blue Book 1973", "https://search.library.wisc.edu/digital/AZKQZSCWLXXUFN8R"),
    1976: ("The Wisconsin Blue Book 1977", "https://search.library.wisc.edu/digital/AXFTVPSYH4JPMZ8D"),
    1980: ("Wisconsin Blue Book 1981-1982", "https://archive.org/details/micro_IA40706910_0093"),
    1984: ("Wisconsin Blue Book 1985-1986", "https://archive.org/details/micro_IA40706923_0147"),
    1988: ("Wisconsin Blue Book 1989-1990", "https://archive.org/details/micro_IA40706936_0250"),
}
METHOD = (
    "Read from the scan by a vision model, every column checked against the printed state total. "
    "Each cell's box is approximate."
)
SLATES = (
    "Through 1964 the Blue Book prints no separate preference line: each figure is the county's vote for the slate "
    "of delegates at large pledged to the candidate, which is how Wisconsin expressed its presidential preference."
)
CAVEATS = {
    (1972, "dem"): "The Blue Book omits the scattering vote.",
    (1972, "rep"): "The Blue Book omits the scattering vote.",
    (1980, "dem"): "The Blue Book omits the scattering vote.",
    (1980, "rep"): "The Blue Book omits the scattering vote.",
    (1984, "dem"): "The Blue Book omits write-in and scattering votes. The Democratic Party did not allocate its "
    "national delegates by this vote but by party caucuses.",
    (1984, "rep"): "The Blue Book omits write-in and scattering votes. The ballot asked Yes or No on Ronald Reagan.",
    (1988, "dem"): "The Blue Book omits the scattering vote.",
    (1988, "rep"): "The Blue Book omits the scattering vote.",
}


def parse() -> None:
    folder = scans.WORK / VOLUME
    review = json.loads((folder / "review.json").read_text())
    if review:
        raise ValueError(f"{len(review)} series still in review: {[item['series'] for item in review]}")
    manifest = scans.load(VOLUME)
    frame = pl.read_parquet(folder / "tables.parquet").with_columns(
        year=pl.col("contest").str.slice(3).cast(pl.Int32),
        party=pl.col("contest").str.slice(0, 3),
        day=pl.col("contest").replace_strict({key: value["day"] for key, value in manifest["contests"].items()}),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("year", "party", "candidate", maintain_order=True)
        .agg(pl.col("votes").sum(), pl.len().alias("counties"))
        .sort("year", "party")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    manifest = scans.load(VOLUME)
    specs = {spec["page"]: spec for spec in manifest["pages"]}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wisconsin's presidential preference primaries by county from the Wisconsin Blue Book, "
            + ", ".join(str(year) for year in sorted(BOOKS)),
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        ((state_unit, state_area),) = conn.execute(
            """select u.id, u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-WI'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = dict(
            conn.execute("select code, id from reporting_unit where scheme_code = 'us.county' and code like '55%%'")
        )
        PAGES.mkdir(parents=True, exist_ok=True)
        for year in sorted(BOOKS):
            book, item = BOOKS[year]
            lines = frame.filter(pl.col("year") == year)
            day = date.fromisoformat(lines["day"][0])
            title = f"{book}, presidential preference vote by county ({day})"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=item,
                license="Public record",
            )
            pages: dict[int, UUID] = {}
            files: dict[str, UUID] = {}
            for (number,) in lines.select("page").unique().sort("page").iter_rows():
                spec = specs[number]
                stored = scans.artifact(manifest, spec)
                if stored.sha256 not in files:
                    files[stored.sha256] = cs.artifact(stored, source)
                each = "{page}" in spec["url"]
                image = PAGES / f"{stored.sha256}-{number}.png"
                if not image.exists():
                    scans.render(manifest, spec, 100).save(image)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                pages[number] = cs.page(files[stored.sha256], 1 if each else number)
                cs.add(
                    "source_page",
                    id=pages[number],
                    artifact_id=files[stored.sha256],
                    page_number=1 if each else number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            election = cs.key("us.election", f"{day}:WI:primary")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-WI"),
                    kind="primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day:%B} {day.day}, {year} Wisconsin presidential preference primary")
                cs.add("slug", path=f"us/wi/{day}-primary", entity_id=election)
            for (owner,), block in lines.group_by("party", maintain_order=True):
                label, scope = PARTIES[str(owner)]
                word = label.lower()
                start = min(FIRST[year], day)
                umbrella = cs.key("us.election", f"{start}:{word}-presidential-primaries")
                national = cs.key("us.contest", f"{start}:president:{word}")
                if umbrella in cs.minted or national in cs.minted:
                    raise ValueError(f"{year} {label} national primaries are not held")
                contest_key = f"{day}:WI:president:{slug(label)}"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="delegate_allocation",
                    office_id=cs.key("us.office", "president"),
                    district_area_id=state_area,
                    party_scope_id=cs.key("us.party", scope),
                    round_number=1,
                    allocation_parent_id=national,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Wisconsin {label} presidential primary")
                cs.add("slug", path=f"us/wi/{day}-primary/president-{slug(label)}", entity_id=contest)
                sets = {
                    grain: cs.key(f"{SOURCE}.result_set", f"{contest_key}:{grain}") for grain in ("state", "county")
                }
                for grain, result_set in sets.items():
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="certified",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=grain == "county",
                        completeness=1,
                    )
                caveat = " ".join(
                    part
                    for part in (
                        METHOD,
                        SLATES if year <= 1964 else None,
                        CAVEATS.get((year, str(owner))),
                    )
                    if part
                )
                cs.add("note", entity_id=sets["county"], kind="caveat", body_md=caveat)
                totals: dict[str, int] = {}
                for (printed, writein), cells in block.group_by("candidate", "writein", maintain_order=True):
                    display = str(printed)
                    kind = (
                        "none_of_the_above"
                        if display == "None of the names shown"
                        else "uncommitted"
                        if display == "Uninstructed delegation"
                        else "answer"
                        if display == "No"
                        else "write_in"
                        if display in ("Scattering", "Write-in")
                        else "candidate"
                    )
                    option_key = f"{contest_key}:{kind}:{slug(display)}"
                    option = cs.key("us.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind=kind,
                        party_id=cs.key("us.party", scope) if kind == "candidate" and not writein else None,
                        ballot_label=f"{display} (write-in)" if kind == "candidate" and writein else display,
                    )
                    if kind == "candidate":
                        person = cs.key(f"{SOURCE}.person", f"WI:{year}:{slug(display)}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, display)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{option_key}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=display,
                            is_independent=False,
                        )
                    state_votes = 0
                    for code, votes, page, x0, y0, x1, y1 in cells.select(
                        "code", "votes", "page", "x0", "y0", "x1", "y1"
                    ).iter_rows():
                        totals[code] = totals.get(code, 0) + votes
                        state_votes += votes
                        fact = {
                            "result_set_id": sets["county"],
                            "unit_id": counties[code],
                            "option_id": option,
                            "channel": 0,
                            "measure": 1,
                            "count_round": 0,
                        }
                        cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                        if x0 is not None:
                            cs.add(
                                "value_locator",
                                **fact,
                                page_id=pages[page],
                                source_id=None,
                                x0=x0,
                                y0=y0,
                                x1=x1,
                                y1=y1,
                            )
                    cs.add(
                        "result_value",
                        result_set_id=sets["state"],
                        unit_id=state_unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=house_books.DIGITIZED,
                        value=state_votes,
                    )
                for grain, unit, value in [
                    ("state", state_unit, sum(totals.values())),
                    *(("county", counties[code], votes) for code, votes in totals.items()),
                ]:
                    cs.add(
                        "result_value",
                        result_set_id=sets[grain],
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=4,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
