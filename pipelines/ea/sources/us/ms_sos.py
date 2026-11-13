import io
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, Connection, connect

SOURCE = "us.ms_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Official election results"
FILES = {
    date(2023, 11, 7): "https://sos.ms.gov/elections/electionresults/2023General/State%20Certified%20Results/"
    "2023%20Statewide%20Recapitulation%20Sheet.pdf",
}
REPORTS = {
    date(2015, 11, 3): "https://sos.ms.gov/elections/electionresults/2015General/certified%20results/"
    "2015%20Electoral%20Vote%20Report.pdf",
    date(2019, 11, 5): "https://sos.ms.gov/elections/electionresults/2019General/Certified%20Results/"
    "Electoral%20Vote%20Report%20(Amended).pdf",
}
SCANS = {
    date(2007, 11, 6): "https://web.archive.org/web/20080306045210id_/http://www.sos.state.ms.us/elections/2007/"
    "Electoral%20Results/Electoral%20Vote%20Report.pdf",
}
PAGES = DATA / "pages" / SOURCE
REPORT_TITLE = "Electoral Vote Assignment"
DISTRICTS = "us.ms_house_district"
OFFICES = {
    "Governor": "Governor",
    "Lieutenant Governor": "Lieutenant Governor",
    "Secretary Of State": "Secretary of State",
    "Attorney General": "Attorney General",
    "State Auditor": "Auditor",
    "State Treasurer": "Treasurer",
    "Commissioner Of Agriculture & Commerce": "Commissioner of Agriculture and Commerce",
    "Commissioner Of Insurance": "Commissioner of Insurance",
}
PARTIES = {
    "Democrat": "democratic-party",
    "Republican": "republican-party",
    "Libertarian": "libertarian-party",
    "Constitution": "constitution-party",
}
CANDIDATE = re.compile(r"^(\S.*?)\s{2,}([A-Z][a-z]+)\s+(\d[\d\s]*)$")


def fetch() -> None:
    for link in [*FILES.values(), *REPORTS.values(), *SCANS.values()]:
        raw.fetch(SOURCE, link, suffix=".pdf")


def pdf_text(link: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, link).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def parse_reports() -> None:
    rows: list[dict[str, object]] = []
    for day, link in REPORTS.items():
        districts: list[int] = []
        office: str | None = None
        printed: dict[str, tuple[int, int]] = {}
        for line in pdf_text(link).splitlines():
            if "MISSISSIPPI SECRETARY OF STATE" in line and "Rep " in line:
                districts = [int(found) for found in re.findall(r"Rep (\d+)", line)]
                continue
            if line.startswith("State Of Mississippi - "):
                office = line.removeprefix("State Of Mississippi - ").strip()
                continue
            found = re.match(r"^(\S.*?)\s{2,}([A-Z][a-z]+)\s+(\d[\d,.\s]*)$", line)
            if office != "Governor" or found is None:
                continue
            numbers = found[3].split()
            pairs = numbers[: 2 * len(districts)]
            if len(numbers) == 2 * len(districts) + 3:
                printed[found[1]] = (int(numbers[-3].replace(",", "")), int(float(numbers[-2])))
            elif len(numbers) != 2 * len(districts):
                raise ValueError(f"{day} {found[1]}: {len(numbers)} numbers for districts {districts}")
            for district, votes, won in zip(districts, pairs[::2], pairs[1::2], strict=True):
                rows.append(
                    {
                        "day": day,
                        "district": district,
                        "candidate": found[1],
                        "party": found[2],
                        "votes": int(votes.replace(",", "")),
                        "won": int(won),
                    }
                )
        check_report(day, pl.DataFrame(rows).filter(pl.col("day") == day), printed)
    frame = pl.concat([pl.DataFrame(rows), parse_scans()], how="diagonal_relaxed")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "districts.parquet")


def check_report(day: date, frame: pl.DataFrame, printed: dict[str, tuple[int, int]]) -> None:
    if sorted(set(frame["district"])) != list(range(1, 123)) or frame.height != 122 * len(printed):
        raise ValueError(f"{day}: districts {frame['district'].n_unique()}, rows {frame.height}")
    summed = frame.group_by("candidate").agg(pl.col("votes").sum(), pl.col("won").sum())
    for candidate, votes, won in summed.iter_rows():
        if printed[candidate] != (votes, won):
            raise ValueError(f"{day} {candidate}: {votes} votes and {won} districts, printed {printed[candidate]}")
    leaders = frame.with_columns(
        top=pl.col("votes") == pl.col("votes").max().over("district"),
    )
    if leaders.filter(pl.col("top").cast(pl.Int64) != pl.col("won")).height:
        raise ValueError(
            f"{day}: a district's electoral vote is not its plurality: {leaders.filter(pl.col('top').cast(pl.Int64) != pl.col('won'))}"
        )


def parse_scans() -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    PAGES.mkdir(parents=True, exist_ok=True)
    line = re.compile(r"^State House Of Rep (\d{3}) ((?:[A-Z][A-Za-z.]* )+)(\S+)(?: (.*))?$")
    total = re.compile(r"^(.+?) ([\d,]+) (\d+) \d+%$")
    totals: dict[date, dict[str, tuple[int, int]]] = {}
    for day, link in SCANS.items():
        scan = raw.cached(SOURCE, link)
        printed = totals.setdefault(day, {})
        number = 0
        while not printed:
            number += 1
            image = PAGES / f"{scan.sha256}-{number}.png"
            if not image.exists():
                subprocess.run(
                    ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(scan.path), str(image.with_suffix(""))],
                    check=True,
                )
            with Image.open(image) as rendered:
                width, height = rendered.size
            words = pl.read_csv(
                io.StringIO(
                    subprocess.run(
                        ["tesseract", str(image), "stdout", "--psm", "6", "tsv"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout
                ),
                separator="\t",
                quote_char=None,
                schema_overrides={"text": pl.String},
            ).filter(pl.col("conf") >= 0, pl.col("text").str.strip_chars() != "")
            lines = words.group_by("block_num", "par_num", "line_num", maintain_order=True).agg(
                "text", "left", "top", "width", "height"
            )
            texts = [" ".join(cells) for cells in lines["text"]]
            found_rows: list[tuple[re.Match[str], list[int], list[int], list[int], list[int]]] = []
            for text, (_, _, _, _, left, top, wide, high) in zip(texts, lines.iter_rows(), strict=True):
                if found := line.match(text):
                    found_rows.append((found, left, top, wide, high))
                elif (found := total.match(text)) and not text.startswith("Candidate"):
                    printed[found[1]] = (int(found[2].replace(",", "")), int(found[3]))
            column = [
                (left[at], left[at] + wide[at])
                for found, left, _, wide, _ in found_rows
                if found[3].isdigit() and (at := 5 + len(found[2].split()))
            ]
            x0, x1 = min(a for a, _ in column), max(b for _, b in column)
            for found, left, top, wide, high in found_rows:
                at = 5 + len(found[2].split())
                box = (left[at], top[at], left[at] + wide[at], top[at] + high[at])
                votes = found[3]
                if not votes.isdigit():
                    box = (x0, top[at - 1], x1, top[at - 1] + high[at - 1])
                    with Image.open(image) as rendered:
                        buffer = io.BytesIO()
                        rendered.crop((box[0] - 12, box[1] - 12, box[2] + 12, box[3] + 12)).save(buffer, "PNG")
                    votes = (
                        subprocess.run(
                            ["tesseract", "stdin", "stdout", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789"],
                            input=buffer.getvalue(),
                            capture_output=True,
                            check=True,
                        )
                        .stdout.decode()
                        .strip()
                    )
                rows.append(
                    {
                        "day": day,
                        "district": int(found[1]),
                        "candidate": found[2].strip(),
                        "votes": int(votes),
                        "read": {"1": 1, "l": 1, "I": 1, "|": 1, "0": 0, "O": 0}.get(found[4] or ""),
                        "page": number,
                        "x0": box[0] / width,
                        "y0": box[1] / height,
                        "x1": box[2] / width,
                        "y1": box[3] / height,
                    }
                )
    frame = pl.DataFrame(rows).with_columns(
        won=(pl.col("votes") == pl.col("votes").max().over("day", "district")).cast(pl.Int64)
    )
    if (wrong := frame.filter(pl.col("read").is_not_null() & (pl.col("read") != pl.col("won")))).height:
        raise ValueError(f"a printed electoral vote is not the district's plurality: {wrong}")
    for day, printed in totals.items():
        check_report(day, frame.filter(pl.col("day") == day), printed)
    return frame.drop("read")


def parse() -> None:
    parse_reports()
    rows: list[dict[str, object]] = []
    for day, link in FILES.items():
        text = pdf_text(link)
        columns: list[str] = []
        placed: list[tuple[int, str]] = []
        extending = False
        office: str | None = None
        heading = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if heading:
                heading = False
                if office is not None and not CANDIDATE.match(line):
                    office = f"{office} {stripped}"
                    continue
            if (
                line.startswith(" ")
                and (extending or re.search(r"\S\s{4,}\S", stripped))
                and not re.search(r"[\d-]", stripped)
            ):
                found_names = [(match.start(), match[0]) for match in re.finditer(r"\S+(?: \S+)*", line)]
                placed = sorted([*placed, *found_names] if extending else found_names)
                columns = [text for _, text in placed]
                extending = True
                continue
            extending = False
            if line.startswith(" ") and "-" in stripped and not re.search(r"\d", stripped):
                parts = stripped.split("-", 1)
                office = parts[1] if parts[0] == "State Of Mississippi" else None
                heading = office is not None
                continue
            found = CANDIDATE.match(line)
            if office is None or found is None or not columns:
                continue
            counts = [int(count) for count in found[3].split()]
            if len(counts) != len(columns):
                raise ValueError(f"{office}: {found[1]} has {len(counts)} counts for {len(columns)} columns")
            for county, count in zip(columns, counts, strict=True):
                if county == "TOTAL":
                    continue
                rows.append(
                    {
                        "day": day,
                        "office": office,
                        "candidate": found[1],
                        "party": found[2],
                        "county": county,
                        "votes": count,
                    }
                )
            if columns[-1] == "TOTAL":
                rows.append(
                    {
                        "day": day,
                        "office": office,
                        "candidate": found[1],
                        "party": found[2],
                        "county": None,
                        "votes": counts[-1],
                    }
                )
    frame = pl.DataFrame(rows)
    unknown = set(frame["office"]) - set(OFFICES)
    if unknown:
        raise ValueError(f"unknown offices: {unknown}")
    counted = (
        frame.filter(pl.col("county").is_not_null()).group_by("day", "office", "candidate").agg(pl.col("votes").sum())
    )
    printed = frame.filter(pl.col("county").is_null()).select("day", "office", "candidate", total="votes")
    checked = counted.join(printed, on=["day", "office", "candidate"], how="full", coalesce=True)
    if checked.filter(pl.col("votes") != pl.col("total")).height or checked["total"].null_count():
        raise ValueError(
            f"counties don't sum to the printed totals: {checked.filter(pl.col('votes') != pl.col('total'))}"
        )
    counties = frame.filter(pl.col("county").is_not_null()).group_by("day", "office").agg(pl.col("county").n_unique())
    if counties.filter(pl.col("county") != 82).height:
        raise ValueError(f"offices without 82 counties: {counties.filter(pl.col('county') != 82)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("county").is_not_null()).with_columns(
        office=pl.col("office").replace_strict(OFFICES)
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mississippi's statewide general elections of 2023 by county, and the 2007, 2015, and 2019 governor"
            " elections by House district",
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
            url="https://www.sos.ms.gov/elections-voting/election-results",
            license="Public record",
        )
        for link in FILES.values():
            cs.artifact(raw.cached(SOURCE, link), source)
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-MS'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties: dict[str, UUID] = {
            re.sub(r"[^a-z]", "", text.lower()): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '28%'"""
            ).fetchall()
        }
        counties["jeffdavis"] = counties["jeffersondavis"]
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the fptp system and the national parties must exist")
        persons: dict[str, UUID] = {}
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            election_key = f"{day}:MS"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-MS"),
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} Mississippi general election")
                cs.add("slug", path=f"us/ms/{day}-general", entity_id=election)
            office_slug = slug(office)
            office_id = cs.key("us.office", f"{office_slug}:MS")
            if f"{office_slug}:MS" not in offices_known:
                offices_known.add(f"{office_slug}:MS")
                cs.add(
                    "office",
                    id=office_id,
                    polity_id=cs.polity("US-MS"),
                    kind="governor" if office == "Governor" else "other",
                )
                name(office_id, f"{office} of Mississippi")
            contest_key = f"{day}:MS:{office_slug}"
            contest = cs.key("us.contest", contest_key)
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
            name(contest, f"{day.year} Mississippi {office} election")
            cs.add("slug", path=f"us/ms/{day}-general/{office_slug}", entity_id=contest)
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
            totals: dict[UUID, int] = {}
            for (candidate, label), cells in lines.group_by("candidate", "party", maintain_order=True):
                if candidate not in persons:
                    persons[candidate] = cs.key(f"{SOURCE}.person", f"MS:{slug(candidate)}")
                    if persons[candidate] in cs.minted:
                        cs.add("person", id=persons[candidate])
                        name(persons[candidate], candidate)
                party = parties.get(label)
                option = cs.key("us.option", f"{contest_key}:{persons[candidate]}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{persons[candidate]}:head"),
                    person_id=persons[candidate],
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=party is None,
                )
                totals[option] = int(cells["votes"].sum())
                for county, votes in cells.select("county", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[re.sub(r"[^a-z]", "", county.lower())],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            leader = max(totals, key=totals.__getitem__)
            if totals[leader] * 2 > sum(totals.values()):
                cs.add("outcome", result_set_id=result_set, option_id=leader, kind="elected", seats=1)
        attach_reports(cs, conn)
        return cs.write()


def attach_reports(cs: Changeset, conn: Connection) -> None:
    frame = pl.read_parquet(PARSED / "districts.parquet")
    source = cs.source(PUBLISHER, REPORT_TITLE)
    cs.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=REPORT_TITLE,
        url="https://www.sos.ms.gov/elections-voting/election-results",
        license="Public record",
    )
    for link in REPORTS.values():
        cs.artifact(raw.cached(SOURCE, link), source)
    pages: dict[tuple[date, int], UUID] = {}
    for day, link in SCANS.items():
        scan = raw.cached(SOURCE, link)
        artifact = cs.artifact(scan, source)
        for (number,) in frame.filter(pl.col("day") == day).select("page").unique().sort("page").iter_rows():
            image = PAGES / f"{scan.sha256}-{number}.png"
            with Image.open(image) as rendered:
                width, height = rendered.size
            pages[(day, number)] = cs.page(artifact, number)
            cs.add(
                "source_page",
                id=pages[(day, number)],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
    ((state,),) = conn.execute(
        """select u.id from reporting_unit u
           join polity_territory t on t.area_id = u.area_id
           join polity p on p.id = t.polity_id and p.iso_code = 'US-MS'
           where u.scheme_code = 'us.state'"""
    ).fetchall()
    cs.add(
        "scheme",
        code=DISTRICTS,
        polity_id=cs.polity("US-MS"),
        kind="electoral_district",
        rank=3,
        noun="house district",
    )
    for (day,), lines in frame.group_by("day", maintain_order=True):
        ((contest, election),) = conn.execute(
            """select c.id, c.election_id
               from contest c join election e on e.id = c.election_id
               join office o on o.id = c.office_id and o.kind = 'governor'
               join polity p on p.id = o.polity_id and p.iso_code = 'US-MS'
               where e.date_from = %s and c.round_number = 1""",
            [day],
        ).fetchall()
        options: dict[str, UUID] = {
            re.sub(r"[^a-z]", "", label.split()[-1].lower()): option
            for option, label in conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'", [contest]
            ).fetchall()
        }
        result_set = cs.key(f"{SOURCE}.result_set", f"{day}:MS:governor:{DISTRICTS}")
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=False,
            completeness=1,
        )
        for district in range(1, 123):
            code = f"{day.year}-{district:03d}"
            cs.add(
                "reporting_unit",
                id=cs.unit(DISTRICTS, code),
                polity_id=cs.polity("US-MS"),
                scheme_code=DISTRICTS,
                code=code,
                parent_id=state,
                area_relation="equals",
                area_id=cs.area("us.sldl", f"MS:{district}"),
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for candidate, district, votes, number, *box in lines.select(
            "candidate", "district", "votes", "page", "x0", "y0", "x1", "y1"
        ).iter_rows():
            fact = {
                "result_set_id": result_set,
                "unit_id": cs.unit(DISTRICTS, f"{day.year}-{district:03d}"),
                "option_id": options[re.sub(r"[^a-z]", "", candidate.split()[-1].lower())],
                "channel": 0,
                "measure": 1,
                "count_round": 0,
            }
            cs.add("result_value", **fact, flags=0 if number is None else house_books.DIGITIZED, value=votes)
            if number is not None:
                x0, y0, x1, y1 = box
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=pages[(day, number)],
                    source_id=None,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )
