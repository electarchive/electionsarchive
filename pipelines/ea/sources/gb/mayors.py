import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import dc_local, leh
from ea.store import DATA, connect

SOURCE = "gb.mayors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
COUNCILS = {
    "bedford": "E06000055",
    "copeland": "E07000029",
    "hackney": "E09000012",
    "leicester": "E06000016",
    "lewisham": "E09000023",
    "mansfield": "E07000174",
    "middlesbrough": "E06000002",
    "newham": "E09000025",
    "tower-hamlets": "E09000030",
    "watford": "E07000103",
}
SV_FROM = date(2000, 5, 4)
SV_TO = date(2023, 5, 4)
TOWER_HAMLETS = "https://democracy.towerhamlets.gov.uk/mgElectionAreaResults.aspx?ID=126&RPID=13851705"
MANSFIELD = "https://www.mansfield.gov.uk/downloads/file/662/mayoral-results-2019"
BEDFORD = "https://www.bedford.gov.uk/files/mayoral.pdf/download?inline="
Box = tuple[float, float, float, float]
ROUND2: dict[str, tuple[str, str, str, str, dict[str, tuple[int, int | None]], int | None]] = {
    "mayor.tower-hamlets.2018-05-03": (
        TOWER_HAMLETS,
        ".html",
        "Tower Hamlets Council",
        "Election results for Tower Hamlets, 3 May 2018: Election of a Mayor of Tower Hamlets",
        {
            "John Biggs": (37619, 44865),
            "Rabina Khan": (13113, 16878),
            "Abul Monsur Ohid Ahmed": (11109, None),
            "Anwara Ali": (6149, None),
            "Elaine Bagshaw": (5598, None),
            "Ciaran Jebb": (3365, None),
            "Hugo Pierre": (728, None),
        },
        None,
    ),
    "mayor.mansfield.2019-05-02": (
        MANSFIELD,
        ".pdf",
        "Mansfield District Council",
        "Declaration of Result, Mayoral election for the District of Mansfield, 2 May 2019",
        {
            "Andy Abrahams": (6881, 7930),
            "Kate Allsop": (5860, 7928),
            "Stephen Garner": (4827, None),
            "George Jabbour": (3592, None),
            "Philip Shields": (2422, None),
        },
        546,
    ),
    "mayor.bedford.2019-05-02": (
        BEDFORD,
        ".pdf",
        "Bedford Borough Council",
        "Statement as to First and Second Preference Votes, Mayoral Election, 2 May 2019",
        {
            "Dave Hodgson": (17596, 21416),
            "Gianni Carofano": (15778, 18105),
            "Adrian Haynes": (2627, None),
            "Jenni Jackson": (9677, None),
            "Adrian Spurrell": (3239, None),
        },
        1001,
    ),
}
ROWS = {
    "Andy Abrahams": (0.181, 0.211),
    "Kate Allsop": (0.211, 0.242),
    "Stephen Garner": (0.242, 0.2725),
    "George Jabbour": (0.2725, 0.3025),
    "Philip Shields": (0.3025, 0.328),
}
COLUMNS = {1: (0.503, 0.649), 2: (0.796, 0.947)}
SCANNED: dict[str, dict[tuple[str | None, int], Box]] = {
    "mayor.mansfield.2019-05-02": {
        **{
            (person, number): (left, top, right, bottom)
            for person, (top, bottom) in ROWS.items()
            for number, (left, right) in COLUMNS.items()
        },
        (None, 0): (0.796, 0.730, 0.944, 0.7575),
    }
}
SCAN_NOTE = (
    "The declaration is a scanned form with handwritten figures; they were transcribed by reading the page image, "
    "and each value's box marks its cell on the form."
)
VOTES, INVALID = 1, 5
MISSING = (
    "No count of second preferences was found in the council's own publications, so only the first round is given."
)


def lines() -> pl.DataFrame:
    frames = []
    for year in dc_local.DAYS:
        try:
            frame = dc_local.result_lines(year)
        except LookupError:
            continue
        frames.append(
            frame.filter(pl.col("election_id").str.starts_with("mayor."))
            .select(
                year=pl.lit(year),
                ballot="election_id",
                council=pl.col("election_id").str.split(".").list.get(1),
                person_id="person_id",
                person_name="person_name",
                party_name="party_name",
                votes=pl.col("ballots_cast").cast(pl.Int64),
                winner=pl.col("is_winner").str.to_lowercase().is_in(["true", "t", "1"]),
                spoilt=pl.col("spoilt_ballots").cast(pl.Int64, strict=False),
                source="source",
            )
            .filter(pl.col("council").is_in(list(COUNCILS)))
        )
    return pl.concat(frames)


def fetch() -> None:
    for url, suffix, _, _, _, _ in ROUND2.values():
        raw.fetch(SOURCE, url, suffix=suffix)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = lines()
    for ballot, group in frame.group_by("ballot"):
        if group["winner"].sum() != 1:
            raise ValueError(f"{ballot}: {group['winner'].sum()} winners")
    for ballot, (url, suffix, _, _, rounds, _) in ROUND2.items():
        group = frame.filter(pl.col("ballot") == ballot)
        if rounds.keys() != set(group["person_name"]):
            raise ValueError(f"{ballot}: the declaration and Democracy Club list different candidates")
        artifact = raw.cached(SOURCE, url)
        text = (
            subprocess.run(["pdftotext", "-layout", str(artifact.path), "-"], check=True, capture_output=True).stdout
            if suffix == ".pdf"
            else artifact.path.read_bytes()
        ).decode(errors="replace")
        printed = set(re.findall(r"\d[\d,]*", text.replace(",", "")))
        figures = {votes for pair in rounds.values() for votes in pair if votes is not None}
        if ballot not in SCANNED and not {str(votes) for votes in figures} <= printed:
            raise ValueError(f"{ballot}: {sorted(figures - {int(f) for f in printed})} not printed in the declaration")
        finals = {person: final for person, (_, final) in rounds.items() if final is not None}
        if len(finals) != 2 or any(final < rounds[person][0] for person, final in finals.items()):
            raise ValueError(f"{ballot}: the second round must carry two candidates, each above their first round")
        (winner,) = group.filter("winner")["person_name"]
        if max(finals, key=finals.__getitem__) != winner:
            raise ValueError(f"{ballot}: the second round's leader is not Democracy Club's winner")
    frame.write_parquet(PARSED / "candidates.parquet")
    print(f"PARSE-DONE {frame['ballot'].n_unique()} ballots, {frame.height} candidates")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Directly elected council mayors of England from Democracy Club's results, with the second round "
            "of each supplementary vote count from the council's declaration",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        fptp = cs.key("gb.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("publish gb.parliament first")
        sv = cs.key("gb.electoral_system", "sv")
        cs.add(
            "electoral_system",
            id=sv,
            polity_id=gb,
            valid_from=SV_FROM,
            valid_to=SV_TO,
            rules={"formula": "supplementary"},
            rules_version=1,
        )
        name(sv, "Supplementary vote")
        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        party_ids: dict[str, UUID | None] = {"Independent": None}
        sources: dict[int, UUID] = {}
        skipped: list[str] = []
        offices: set[UUID] = set()
        for (ballot,), group in frame.sort("ballot").group_by("ballot", maintain_order=True):
            first = group.row(0, named=True)
            year, code = first["year"], COUNCILS[first["council"]]
            day = dc_local.DAYS[year]
            found = conn.execute(
                "select entity_id from external_id where scheme = 'gb.election' and value = %s",
                [f"{day}:local:{code}"],
            ).fetchone()
            if found is None:
                skipped.append(ballot)
                continue
            election = cs.key("gb.election", f"{day}:local:{code}")
            polity = cs.key("gb.polity", code)
            area = cs.area("gb.lad", code)
            council_name, path = conn.execute(
                "select n.text, s.path from name n join slug s on s.entity_id = n.entity_id"
                " where n.entity_id = %s and n.is_preferred order by s.is_current desc limit 1",
                [polity],
            ).fetchone()
            place = council_name.removesuffix(" City Council").removesuffix(" Council")
            if year not in sources:
                title = f"Candidate results, {day}"
                sources[year] = cs.source(dc_local.DC, title)
                if sources[year] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[year],
                        kind="compendium",
                        publisher=dc_local.DC,
                        title=title,
                        url=(dc_local.RESULTS if year in dc_local.ARCHIVED else dc_local.API).format(day=day),
                        license=dc_local.LICENSE,
                        license_url=dc_local.LICENSE_URL,
                        attribution=dc_local.ATTRIBUTION,
                        notes=dc_local.METHODOLOGY,
                    )
                    for url in dc_local.result_pages(year):
                        cs.artifact(raw.cached(dc_local.SOURCE, url), sources[year])
            office = cs.key("gb.office", f"mayor:{code}")
            if office not in offices:
                offices.add(office)
                cs.add("office", id=office, polity_id=polity, body_id=None, kind="mayor")
                name(office, f"Mayor of {place}")
            unit = cs.unit("gb.lad", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="gb.lad",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, place)
            supplementary = SV_FROM <= day < SV_TO
            contest_key = f"{day}:mayor:{code}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                body_id=None,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=sv if supplementary else fptp,
                default_result_kind="counted",
                term="full",
            )
            name(contest, f"Mayor of {place}, {year}")
            cs.add("slug", path=f"{path}/{day}-local/mayor", entity_id=contest)
            cs.add("external_id", entity_id=contest, scheme="democracyclub.ballot", value=ballot)
            rows = group.sort("votes", descending=True).rows(named=True)
            options: dict[str, UUID] = {}
            for row in rows:
                label = row["party_name"] or "Independent"
                if label not in party_ids:
                    found = (
                        cs.key(*leh.RESOLVED[label])
                        if label in leh.RESOLVED
                        else known.get(leh.fold(dc_local.ALIASES.get(label, label)))
                    )
                    if found is None:
                        found = cs.key("gb.party", slug(label))
                        if found in cs.minted and found not in party_ids.values():
                            cs.add("party", id=found, kind="party", polity_id=gb)
                            name(found, label)
                    party_ids[label] = found
                owner = party_ids[label]
                option_key = f"{contest_key}:{row['person_id']}"
                option = options[row["person_id"]] = cs.key("gb.mayors.option", option_key)
                person = cs.key("gb.dc_local.person", row["person_id"])
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, row["person_name"])
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=owner,
                    ballot_label=row["person_name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.mayors.candidacy", option_key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["person_name"],
                    member_of_party_id=owner,
                    is_independent=label == "Independent",
                )
            ids = {row["person_name"]: row["person_id"] for row in rows}
            winner = next(row["person_id"] for row in rows if row["winner"])
            first_round = 1 if supplementary else 0
            dc_values = {row["person_id"]: {first_round: row["votes"]} for row in rows}
            sets: list[
                tuple[
                    UUID,
                    UUID,
                    bool,
                    dict[str, dict[int, int]],
                    int | None,
                    dict[tuple[str | None, int], Box],
                    UUID | None,
                ]
            ] = []
            second = ROUND2.get(ballot)
            council_values: dict[str, dict[int, int]] = {}
            if second is not None:
                url, _, publisher, title, rounds, rejected = second
                council_values = {ids[person]: {1: votes, 2: final or 0} for person, (votes, final) in rounds.items()}
                council_source = cs.source(publisher, title)
                cs.add("source", id=council_source, kind="official_body", publisher=publisher, title=title, url=url)
                artifact = raw.cached(SOURCE, url)
                artifact_id = cs.artifact(artifact, council_source)
                boxes = {
                    (None if person is None else ids[person], number): box
                    for (person, number), box in SCANNED.get(ballot, {}).items()
                }
                page = None
                if boxes:
                    image = PAGES / f"{artifact.sha256}-1.png"
                    if not image.exists():
                        PAGES.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            ["pdftoppm", "-r", "100", "-png", "-f", "1", "-l", "1", "-singlefile"]
                            + [str(artifact.path), str(image.with_suffix(""))],
                            check=True,
                        )
                    width, height = struct.unpack(">II", image.read_bytes()[16:24])
                    page = cs.page(artifact_id, 1)
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifact_id,
                        page_number=1,
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
                council_set = cs.key("gb.mayors.council_result_set", contest_key)
                sets.append((council_set, council_source, True, council_values, rejected, boxes, page))
                if boxes:
                    cs.add("note", entity_id=council_set, kind="methodology", body_md=SCAN_NOTE)
                differing = [row for row in rows if council_values[row["person_id"]][1] != row["votes"]]
                for row in differing:
                    cs.add(
                        "note",
                        entity_id=council_set,
                        kind="caveat",
                        body_md=f"Democracy Club gives {row['person_name']} {row['votes']:,} votes; the declaration "
                        f"gives {council_values[row['person_id']][1]:,} first preferences.",
                    )
                if not differing:
                    sets.append(
                        (
                            cs.key("gb.mayors.result_set", contest_key),
                            sources[year],
                            False,
                            dc_values,
                            first["spoilt"],
                            {},
                            None,
                        )
                    )
            else:
                sets.append(
                    (
                        cs.key("gb.mayors.result_set", contest_key),
                        sources[year],
                        True,
                        dc_values,
                        first["spoilt"],
                        {},
                        None,
                    )
                )
            firsts = {person: values.get(first_round, 0) for person, values in sets[0][3].items()}
            outright = not supplementary or firsts[winner] * 2 > sum(firsts.values())
            for result_set, source, default, values, invalid, boxes, page in sets:
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                if source == sources[year]:
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=source,
                        locator={"ballot_paper_id": ballot, "council_source": first["source"]},
                    )
                    if not outright and second is None:
                        cs.add("note", entity_id=result_set, kind="caveat", body_md=MISSING)
                cells: list[tuple[UUID | None, int, int, int]] = []
                if invalid is not None:
                    cells.append((None, INVALID, 0, invalid))
                for row in rows:
                    for number, votes in values[row["person_id"]].items():
                        cells.append((options[row["person_id"]], VOTES, number, votes))
                    if row["person_id"] == winner:
                        kinds = ("won", "elected")
                    elif 2 in values[row["person_id"]] and not values[row["person_id"]][2]:
                        kinds = ("eliminated",)
                    else:
                        kinds = ("lost",)
                    for kind in kinds:
                        cs.add("outcome", result_set_id=result_set, option_id=options[row["person_id"]], kind=kind)
                by_option = {option: person for person, option in options.items()}
                for option, measure, number, votes in cells:
                    box = boxes.get((None if option is None else by_option[option], number))
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=number,
                        flags=8 if box is not None else 0,
                        value=votes,
                    )
                    if box is not None:
                        x0, y0, x1, y1 = box
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
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
        if skipped:
            print(f"held until their council election publishes: {', '.join(skipped)}")
        return cs.write()
