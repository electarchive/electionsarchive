import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.statewide_totals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FOLDER = Path(__file__).with_name("totals")
DPI = 100
PARTIES = {
    "democratic": "democratic-party",
    "democrat": "democratic-party",
    "republican": "republican-party",
    "whig": "whig-party",
    "libertarian": "libertarian-party",
    "green": "green-party",
    "prohibition": "prohibition-party",
    "socialist labor": "socialist-labor-party-of-america",
    "constitution": "constitution-party",
    "reform": "reform-party",
    "american independent": "american-independent-party",
    "natural law": "natural-law-party",
    "greenback": "greenback-party",
    "populist": "peoples-party",
    "people's": "peoples-party",
    "socialist": "socialist-party-of-america",
    "socialist workers": "socialist-workers-party",
    "socialist party usa": "socialist-party-usa",
    "free soil": "free-soil-party",
    "know nothing": "know-nothing-party",
}
INDEPENDENT = {"independent", "nonpartisan", "none", "no party", "scattering", "write-in", "write-ins"}


def sources() -> pl.DataFrame:
    return pl.read_csv(FOLDER / "sources.tsv", separator="\t", schema_overrides={"page_offset": pl.Int64}).with_columns(
        pl.col("page_offset").fill_null(0)
    )


def fetch() -> None:
    for url, suffix in sources().select("url", "suffix").iter_rows():
        raw.fetch(SOURCE, url, suffix=suffix)


def parse() -> None:
    known = set(sources()["id"])
    files = sorted(path for path in FOLDER.glob("*.tsv") if path.name != "sources.tsv")
    frame = pl.concat(
        [
            pl.read_csv(
                path,
                separator="\t",
                schema_overrides={"party": pl.String, "winner": pl.Int64, "page": pl.Int64, "votes": pl.Int64},
            )
            for path in files
        ]
    )
    contest = ["state", "date", "office"]
    if unknown := set(frame["source"]) - known:
        raise ValueError(f"sources not in sources.tsv: {unknown}")
    if not (doubled := frame.group_by(*contest, "name").len().filter(pl.col("len") > 1)).is_empty():
        raise ValueError(f"names printed twice in one contest: {doubled}")
    if frame.filter(pl.col("votes") < 0).height or frame["votes"].null_count():
        raise ValueError("a vote count is missing or negative")
    if not (many := frame.group_by(*contest).agg(pl.col("source").n_unique()).filter(pl.col("source") > 1)).is_empty():
        raise ValueError(f"a contest cites two sources: {many}")
    flagged = frame.group_by(*contest).agg(pl.col("winner").fill_null(0).sum())
    if not (bad := flagged.filter(pl.col("winner") > 1)).is_empty():
        raise ValueError(f"more than one winner: {bad}")
    ranked = (
        frame.sort("votes", descending=True)
        .group_by(*contest, maintain_order=True)
        .agg(
            pl.col("name").first().alias("leader"),
            pl.col("name").filter(pl.col("winner") == 1).first().alias("declared"),
            (pl.col("votes").first() == pl.col("votes").get(1)).alias("tied"),
        )
    )
    for row in ranked.filter(pl.col("declared").is_not_null() & (pl.col("declared") != pl.col("leader"))).iter_rows():
        print("declared winner is not the leader:", row)
    if not (tied := ranked.filter(pl.col("declared").is_null() & pl.col("tied").fill_null(False))).is_empty():
        raise ValueError(f"a tie with no declared winner: {tied}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("state", "office").agg(pl.col("date").n_unique().alias("years")).sort("state", "office"))


def emit(
    pipeline: str = SOURCE,
    version: str = VERSION,
    summary: str = "Statewide executive offices by state total from state manuals and blue books",
    frame: pl.DataFrame | None = None,
    book: dict[str, dict[str, str | int]] | None = None,
    note: str | None = None,
) -> Path:
    if frame is None:
        frame = pl.read_parquet(PARSED / "lines.parquet")
    if book is None:
        book = {row["id"]: row for row in sources().iter_rows(named=True)}
    with connect() as conn:
        cs = Changeset(conn, pipeline=pipeline, version=version, summary=summary, polity="US")

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
            return plain(words[-1]) if words else ""

        state_units = {
            code: (unit, area, text)
            for code, unit, area, text in conn.execute(
                """select substr(p.iso_code, 4), u.code, u.area_id, n.text from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%%'
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        ours = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = %s", [f"{pipeline}.result_set"])
        }
        held = {
            (office, year)
            for office, year, contest_key in conn.execute(
                """select o.value, extract(year from e.date_from)::int, k.value from contest c
                   join election e on e.id = c.election_id
                   join external_id o on o.entity_id = c.office_id and o.scheme = 'us.office'
                   join external_id k on k.entity_id = c.id and k.scheme = 'us.contest'"""
            )
            if contest_key not in ours
        }
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        slugs = {value for (value,) in conn.execute("select path from slug")}
        fptp = cs.key("us.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("the electoral system must already exist")
        minted: set[UUID] = set()
        people: dict[str, dict[str, list[tuple[UUID, int]]]] = {}
        sources_made: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        page_ids: dict[tuple[str, int], UUID] = {}
        filled = 0
        skipped = 0

        def party(state: str, label: str | None) -> tuple[UUID | None, bool]:
            if label is None or label.lower() in INDEPENDENT:
                return None, label is not None
            if (national := PARTIES.get(label.lower().removesuffix(" party"))) is not None:
                return cs.key("us.party", national), False
            text = label if re.search(r"\bparty\b", label, re.IGNORECASE) else f"{label} Party"
            options = [f"{state}:{slug(text)}", f"{state}:{slug(text.removesuffix(' Party'))}", slug(text)]
            key = next((key for key in options if key in parties_known), options[0])
            found = cs.key("us.party", key)
            if key not in parties_known:
                parties_known.add(key)
                cs.add("party", id=found, kind="party", polity_id=cs.polity(f"US-{state}"))
                name(found, text)
            return found, False

        for (state, day, office, source_id), lines in frame.sort("state", "date", "office").group_by(
            "state", "date", "office", "source", maintain_order=True
        ):
            year = int(str(day)[:4])
            unit_code, area, state_name = state_units[str(state)]
            title = str(lines["title"][0])
            key = f"{day}:{state}:{office}"
            if (f"{office}:{state}", year) in held:
                print(f"{key}: held by another pipeline, skipped")
                skipped += 1
                continue
            if f"{day}:{state}" not in elections:
                election = cs.key("us.election", f"{day}:{state}")
                elections[f"{day}:{state}"] = election
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="general",
                    date_from=date.fromisoformat(str(day)),
                    date_to=date.fromisoformat(str(day)),
                    status="held",
                )
                name(election, f"{year} {state_name} general election")
                path = f"us/{str(state).lower()}/{day}-general"
                if path in slugs:
                    raise ValueError(f"slug taken: {path}")
                slugs.add(path)
                cs.add("slug", path=path, entity_id=election)
            election = elections[f"{day}:{state}"]
            if source_id not in sources_made:
                row = book[str(source_id)]
                made = cs.source(row["publisher"], row["title"])
                sources_made[str(source_id)] = made
                if made in cs.minted:
                    cs.add(
                        "source",
                        id=made,
                        kind=row.get("kind", "official_body"),
                        publisher=row["publisher"],
                        title=row["title"],
                        url=row["landing"],
                        license=row["license"],
                    )
                artifacts[str(source_id)] = cs.artifact(raw.cached(pipeline, row["url"]), made)
            office_key = f"{office}:{state}"
            office_id = cs.key("us.office", office_key)
            if office_key not in offices_known:
                offices_known.add(office_key)
                cs.add("office", id=office_id, polity_id=cs.polity(f"US-{state}"), kind="other")
                name(office_id, f"{title} of {state_name}")
            if str(state) not in people:
                catalog: dict[str, list[tuple[UUID, int]]] = {}
                for person, text, seen in conn.execute(
                    """select distinct cd.person_id, n.text, extract(year from e.date_from)::int from candidacy cd
                       join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
                       join election e on e.id = c.election_id
                       join name n on n.entity_id = cd.person_id and n.is_preferred
                       where c.district_area_id = %s""",
                    [area],
                ).fetchall():
                    if text.split():
                        catalog.setdefault(f"{plain(text.split()[0])}|{surname(text)}", []).append((person, seen))
                people[str(state)] = catalog
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{year} {state_name} {title} election")
            path = f"us/{str(state).lower()}/{day}-general/{office}"
            cs.add("slug", path=path, entity_id=contest)
            result_set = cs.key(f"{pipeline}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources_made[str(source_id)],
                is_default=True,
                completeness=1,
            )
            if note is not None:
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            totals: dict[UUID, int] = {}
            declared: UUID | None = None
            for candidate, party_label, votes, winner, page in (
                lines.sort("votes", descending=True).select("name", "party", "votes", "winner", "page").iter_rows()
            ):
                probe = f"{plain(candidate.split()[0])}|{surname(candidate)}"
                found = {person for person, seen in people[str(state)].get(probe, []) if abs(seen - year) <= 16}
                person = (
                    next(iter(found))
                    if len(found) == 1
                    else cs.key(f"{pipeline}.person", f"{state}:{slug(candidate)}:{year}")
                )
                if person in cs.minted and person not in minted:
                    minted.add(person)
                    cs.add("person", id=person)
                    name(person, candidate)
                    people[str(state)].setdefault(probe, []).append((person, year))
                label, independent = party(str(state), party_label)
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=label,
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=independent,
                )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", unit_code),
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=8 if page is not None else 0,
                    value=votes,
                )
                if page is not None:
                    row = book[str(source_id)]
                    number = int(page) + int(row["page_offset"])
                    if (str(source_id), number) not in page_ids:
                        pdf = raw.cached(pipeline, row["url"]).path
                        target = DATA / "pages" / pipeline / f"{pdf.stem}-{number}.png"
                        if not target.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            subprocess.run(
                                ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(number), "-l", str(number)]
                                + ["-singlefile", str(pdf), str(target.with_suffix(""))],
                                check=True,
                            )
                        with Image.open(target) as rendered:
                            width, height = rendered.size
                        page_ids[(str(source_id), number)] = cs.page(artifacts[str(source_id)], number)
                        cs.add(
                            "source_page",
                            id=page_ids[(str(source_id), number)],
                            artifact_id=artifacts[str(source_id)],
                            page_number=number,
                            image_key=f"pages/{pipeline}/{target.name}",
                            width=width,
                            height=height,
                        )
                    cs.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.state", unit_code),
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        page_id=page_ids[(str(source_id), number)],
                        source_id=None,
                        x0=0.0,
                        y0=0.0,
                        x1=1.0,
                        y1=1.0,
                    )
                totals[option] = votes
                if winner == 1:
                    declared = option
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=declared if declared is not None else max(totals, key=totals.__getitem__),
                kind="elected",
                seats=1,
            )
            filled += 1
        print(filled, "contests,", skipped, "skipped")
        return cs.write()
