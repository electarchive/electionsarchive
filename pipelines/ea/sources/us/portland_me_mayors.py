import json
import re
import subprocess
import tempfile
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageOps

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.portland_me_mayors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "City of Portland, Maine City Clerk"
TABULATION = "http://www.portlandmaine.gov/voter/portlandofficialrcvresults.pdf"
CAPTURE = f"https://web.archive.org/web/20120216132821id_/{TABULATION}"
ASSET = "https://content.civicplus.com/api/assets/me-portland/{}"
SCANS: dict[str, dict] = {
    "2015-11-03": {
        "asset": "fc44b3f4-3e2e-4833-841e-c10e7228e82b",
        "title": "Nov 3 2015 General Election Official Results",
        "page": 6,
        "rounds": {"Brennan": [6884], "MacMillan": [1883], "Strimling": [9163]},
        "precincts": {
            "Brennan": [655, 344, 198, 773, 490, 789, 686, 860, 374, 396, 647, 672],
            "MacMillan": [246, 158, 35, 233, 201, 170, 133, 210, 70, 93, 158, 176],
            "Strimling": [693, 464, 207, 881, 444, 885, 989, 1113, 727, 652, 1125, 983],
        },
    },
    "2019-11-05": {
        "asset": "3242de16-ac41-4f67-b57e-1c0dfbb66662",
        "title": "Nov 5 2019 General Election Official Results",
        "page": 1,
        "rounds": {
            "Curran, Travis H.": [1296, 0, 0],
            "Snyder, Kathleen M.": [7119, 7577, 10460],
            "Strimling, Ethan K.": [4575, 5155, 0],
            "Thibodeau, Spencer R.": [5110, 5274, 6442],
        },
        "overvotes": 39,
        "undervotes": 136,
        "continuing": [18100, 18006, 16902],
        "ballots": 18275,
    },
}
TITLE_2011 = (
    "November 8, 2011 Municipal Election, official results: ranked choice voting election for mayor of Portland"
)
METHOD_2011 = "Data comes from the city's official ranked-choice tabulation, produced by TrueBallot and posted on November 10, 2011, after a full review. Blank ballots and overvotes are counted as invalid; ballots that stop counting in later rounds are counted as exhausted. Write-ins are grouped into one line, which was eliminated in round 1."
METHOD_2015 = "Data comes from the city clerk's official results, which exist only as a scanned page. Each number was read by OCR and checked against a transcription and the precinct rows. Ethan Strimling won a majority of first choices, so no further rounds were counted. The page lists each candidate's total but not blank, overvoted, or write-in ballots."
METHOD_2019 = "Data comes from the city clerk's official ranked-choice summary report, signed on November 6, 2019. The report exists only as a scanned page. Each number was read by OCR and checked against a transcription. Every round adds up to its continuing ballots and the ballot total. Overvotes and undervotes in round 1 are counted as invalid; ballots that stop counting in later rounds are counted as exhausted."
DEMOTED = "Lists each candidate's total from their last round, as compiled by the American Local Government Elections Database. The city clerk's official count is the default."


def fetch() -> None:
    raw.fetch(SOURCE, CAPTURE, suffix=".pdf")
    for scan in SCANS.values():
        raw.fetch(SOURCE, ASSET.format(scan["asset"]), suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CAPTURE).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    if "OFFICIAL RESULTS: Ranked choice voting election for mayor of Portland" not in text:
        raise ValueError("unexpected 2011 tabulation")
    rounds: list[dict[str, int]] = []
    totals: dict[str, list[int]] = {}
    for line in text[text.index("Candidate") : text.index("Total ballots scanned")].splitlines()[1:]:
        if match := re.fullmatch(r"(Total valid|Exhausted|Ballots|[A-Z][\w-]+)\s+(\d.*?)(?:\s+[\d.]+%)?", line.strip()):
            cells = [int(cell.replace(",", "")) for cell in re.findall(r"[\d,]+", match[2])]
            if match[1] in ("Total valid", "Exhausted", "Ballots"):
                totals[match[1]] = cells
                continue
            while len(rounds) < len(cells):
                rounds.append({})
            for number, votes in enumerate(cells):
                rounds[number][match[1]] = votes
    labels = list(rounds[0])
    rounds = [{label: tally.get(label, 0) for label in labels} for tally in rounds]
    accounting = {
        label: int(value.replace(",", ""))
        for label, value in re.findall(r"(Total ballots scanned|Valid ballots|Blanks|overvotes\))\s+([\d,]+)", text)
    }
    valid = accounting["Valid ballots"]
    invalid = accounting["Blanks"] + accounting["overvotes)"]
    if valid + invalid != accounting["Total ballots scanned"] or len(rounds) != 15:
        raise ValueError("2011 ballot accounting does not add up")
    for number, tally in enumerate(rounds):
        if sum(tally.values()) != totals["Total valid"][number] or totals["Ballots"][number] != valid:
            raise ValueError(f"2011 round {number + 1} does not add up")
        if totals["Exhausted"][number] != valid - sum(tally.values()):
            raise ValueError(f"2011 round {number + 1} exhausted")
    contests: dict[str, dict] = {
        "2011-11-08": {"ballots": accounting["Total ballots scanned"], "invalid": invalid, "rounds": rounds}
    }

    for day, scan in SCANS.items():
        pdf = raw.cached(SOURCE, ASSET.format(scan["asset"])).path
        with tempfile.TemporaryDirectory() as folder:
            stem = Path(folder) / "page"
            subprocess.run(
                ["pdftoppm", "-r", "300", "-png", "-f", str(scan["page"]), "-l", str(scan["page"]), "-singlefile"]
                + [str(pdf), str(stem)],
                check=True,
            )
            upright = Image.open(stem.with_suffix(".png")).rotate(90, expand=True)
            upright.save(stem.with_suffix(".png"))
            width, height = upright.size
            tsv = subprocess.run(
                ["tesseract", str(stem.with_suffix(".png")), "-", "--psm", "11", "tsv"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        words = []
        for line in tsv.splitlines()[1:]:
            cells = line.split("\t")
            if len(cells) == 12 and cells[11].strip():
                left, top, wide, tall = (int(cell) for cell in cells[6:10])
                words.append((cells[11].strip(), left, top, left + wide, top + tall))

        def box(word: tuple[str, int, int, int, int], width: int = width, height: int = height) -> list[float]:
            return [word[1] / width, word[2] / height, word[3] / width, word[4] / height]

        def center(word: tuple[str, int, int, int, int]) -> float:
            return (word[1] + word[3]) / 2

        rows: dict[str, list[dict]] = {}
        gray = upright.convert("L")
        pixels = gray.tobytes()

        def read(
            crop: tuple[int, int, int, int], expected: list[int], folder: str, gray: Image.Image = gray, box=box
        ) -> list[float]:
            raw_cell = gray.crop(crop)
            cell = raw_cell.copy()
            wide, tall = cell.size
            ink = cell.tobytes()
            for y in range(tall):
                if sum(value < 160 for value in ink[y * wide : (y + 1) * wide]) > wide * 0.5:
                    cell.paste(255, (0, y, wide, y + 1))
            ink = cell.tobytes()
            for x in range(wide):
                if sum(ink[y * wide + x] < 160 for y in range(tall)) > tall * 0.8:
                    cell.paste(255, (x, 0, x + 1, tall))
            readings = []
            variants = [
                (raw_cell, 0, 1),
                (cell, 0, 1),
                (ImageOps.expand(cell, 20, 255), 20, 1),
                (ImageOps.expand(raw_cell.point(lambda value: 0 if value < 140 else 255), 20, 255), 20, 1),
                (ImageOps.expand(raw_cell.resize((wide * 2, tall * 2), Image.Resampling.LANCZOS), 20, 255), 20, 2),
            ]
            for variant, margin, scale in variants:
                variant.save(Path(folder) / "cell.png")
                for mode in ("7", "8", "10"):
                    tsv = subprocess.run(
                        ["tesseract", str(Path(folder) / "cell.png"), "-", "--psm", mode]
                        + ["-c", "tessedit_char_whitelist=0123456789", "tsv"],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout
                    found = [line.split("\t") for line in tsv.splitlines()[1:] if line.split("\t")[-1].strip()]
                    readings.append([int(cells[11]) for cells in found])
                    if readings[-1] == expected:
                        x, y, wide, tall = (int(value) for value in found[0][6:10])
                        x, y, wide, tall = (x - margin) // scale, (y - margin) // scale, wide // scale, tall // scale
                        return box(("", crop[0] + x, crop[1] + y, crop[0] + x + wide, crop[1] + y + tall))
            raise ValueError(f"OCR read {readings} at {crop}, transcribed {expected}")

        if day == "2015-11-03":
            heads = sorted((word for word in words if word[0].startswith(tuple(scan["rounds"]))), key=center)
            labels = [label for head in heads for label in scan["rounds"] if head[0].startswith(label)]
            if labels != list(scan["rounds"]):
                raise ValueError(f"2015: headers read as {[head[0] for head in heads]}")
            band = max(head[4] for head in heads)
            rules: list[int] = []
            for x in range(heads[0][1] - 200, width):
                if sum(pixels[y * width + x] < 160 for y in range(band - 50, band + 120)) > 170 * 0.85:
                    if rules and x - rules[-1] <= 3:
                        rules[-1] = x
                    else:
                        rules.append(x)
            rules = rules[:4] if len(rules) >= 4 else [*rules, rules[-1] + rules[-1] - rules[-2]]
            with tempfile.TemporaryDirectory() as folder:
                for label, left, right in zip(labels, rules[:-1], rules[1:], strict=True):
                    expected = scan["rounds"][label]
                    if sum(scan["precincts"][label]) != expected[0]:
                        raise ValueError(f"2015 {label}: the precinct rows do not add up to {expected}")
                    located = read((left + 10, band - 10, right - 10, band + 60), expected, folder)
                    rows[label] = [{"votes": expected[0], "box": located}]
            contests[day] = {"rows": rows}
            continue
        rules = []
        for x in range(width):
            if sum(pixels[y * width + x] < 160 for y in range(height)) > height * 0.25:
                if rules and x - rules[-1] <= 3:
                    rules[-1] = x
                else:
                    rules.append(x)
        rules = [x for x in rules if 10 < x < width - 10]
        if len(rules) != 10:
            raise ValueError(f"{day}: found {len(rules)} table rules")
        anchors = {label: label.split()[0] for label in scan["rounds"]}
        anchors |= {"overvotes": "Overvotes", "undervotes": "Undervotes", "continuing": "Continuing"}
        tops = {}
        for label, text in anchors.items():
            (anchor,) = [word for word in words if word[0] == text and word[1] < rules[0]]
            tops[label] = anchor[2]
        first, second = list(scan["rounds"])[:2]
        tops["ballots"] = tops["continuing"] + tops[second] - tops[first]
        expected_rows = {
            **scan["rounds"],
            "overvotes": [scan["overvotes"]],
            "undervotes": [scan["undervotes"]],
            "continuing": scan["continuing"],
            "ballots": [scan["ballots"]],
        }
        with tempfile.TemporaryDirectory() as folder:
            for label, expected in expected_rows.items():
                rows[label] = []
                for number, votes in enumerate(expected):
                    left, right = rules[3 * number], rules[3 * number + 1]
                    located = read((left + 8, tops[label] - 18, right - 8, tops[label] + 45), [votes], folder)
                    rows[label].append({"votes": votes, "box": located})
        for number, continuing in enumerate(scan["continuing"]):
            if sum(cells[number] for cells in scan["rounds"].values()) != continuing:
                raise ValueError(f"{day} round {number + 1} does not add up")
        if scan["overvotes"] + scan["undervotes"] + scan["continuing"][0] != scan["ballots"]:
            raise ValueError(f"{day}: round 1 does not add up to the ballot total")
        contests[day] = {"rows": rows}
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Portland, Maine's 2011, 2015, and 2019 mayoral races from the city's official ranked-choice "
            "tabulation and the clerk's scanned official results",
            polity="US",
        )

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "2360545")
        top = cs.unit("us.place", "2360545")
        if {city, top} & cs.minted:
            raise ValueError("Portland, Maine must already exist")
        ((polity,),) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchall()
        irv = cs.key("us.electoral_system", "portland-me:irv")
        if irv in cs.minted:
            raise ValueError("Portland, Maine's IRV system must already exist")
        people: dict[str, set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id where e.polity_id = %s""",
            [polity],
        ).fetchall():
            people.setdefault(spell(text)[-1], set()).add(person)
        columns = (
            "election_id",
            "kind",
            "office_id",
            "body_id",
            "district_area_id",
            "seat_id",
            "boundary_set_id",
            "magnitude",
            "party_scope_id",
            "round_number",
            "previous_round_id",
            "allocation_parent_id",
            "rules_override",
            "default_result_kind",
            "term",
        )
        fields = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness")
        fields += ("supersedes_id",)

        for day, record in contests.items():
            year = day[:4]
            ((contest,),) = conn.execute(
                """select k.id from contest k join name n on n.entity_id = k.id join election e on e.id = k.election_id
                   where n.text = %s and e.polity_id = %s and e.date_from = %s""",
                [f"{year} Portland mayoral election", polity, date.fromisoformat(day)],
            ).fetchall()
            (row,) = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchall()
            election = row[0]
            cs.add("contest", id=contest, **dict(zip(columns, row, strict=True)), electoral_system_id=irv)
            for values in conn.execute(
                f"""select {", ".join(fields)} from result_set where contest_id = %s
                    and changeset_id not in (select id from changeset where pipeline = %s)""",
                [contest, SOURCE],
            ).fetchall():
                cs.add("result_set", **dict(zip(fields, values, strict=True)), is_default=False)
                cs.add("note", entity_id=values[0], kind="caveat", body_md=DEMOTED)
            current: dict[str, UUID] = {}
            for option, person, label in conn.execute(
                """select o.id, c.person_id, o.ballot_label from contest_option o
                   join candidacy c on c.option_id = o.id where o.contest_id = %s and o.kind = 'candidate'""",
                [contest],
            ).fetchall():
                current[spell(label)[-1]] = option
                people[spell(label)[-1]] = {person}

            if day == "2011-11-08":
                url, title, method, scan = TABULATION, TITLE_2011, METHOD_2011, None
                artifact = raw.cached(SOURCE, CAPTURE)
                rounds = record["rounds"]
                cells: dict[str, list[dict]] = {
                    label: [{"votes": tally[label], "box": None} for tally in rounds] for label in rounds[0]
                }
                ballots = (record["ballots"], None)
                invalid = (record["invalid"], None)
            else:
                scan = SCANS[day]
                url, title = ASSET.format(scan["asset"]), scan["title"]
                method = METHOD_2015 if year == "2015" else METHOD_2019
                artifact = raw.cached(SOURCE, url)
                rows = record["rows"]
                cells = {label: rows[label] for label in scan["rounds"]}
                ballots = (rows["ballots"][0]["votes"], rows["ballots"][0]["box"]) if "ballots" in rows else None
                invalid = (
                    (rows["overvotes"][0]["votes"] + rows["undervotes"][0]["votes"], None)
                    if "overvotes" in rows
                    else None
                )
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record",
            )
            artifact_id = cs.artifact(artifact, source)
            page = None
            if scan is not None:
                page = cs.page(artifact_id, scan["page"])
                image = PAGES / f"{artifact.sha256}-{scan['page']}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory() as folder:
                        stem = Path(folder) / "page"
                        subprocess.run(
                            ["pdftoppm", "-r", "100", "-png", "-f", str(scan["page"]), "-l", str(scan["page"])]
                            + ["-singlefile", str(artifact.path), str(stem)],
                            check=True,
                        )
                        Image.open(stem.with_suffix(".png")).rotate(90, expand=True).save(image)
                with Image.open(image) as opened:
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifact_id,
                        page_number=scan["page"],
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=opened.width,
                        height=opened.height,
                    )

            options: dict[str, UUID] = {}
            for label in cells:
                if label == "Write-in":
                    options[label] = cs.key("us.option", f"{contest}:write_in:scattered")
                    cs.add("contest_option", id=options[label], contest_id=contest, kind="write_in", ballot_label=label)
                    continue
                surname = spell(label.split(",")[0])[-1]
                if surname in current:
                    options[label] = current[surname]
                    continue
                last, first = label.split(", ")
                text = f"{first.split()[0]} {last}"
                if len(people.get(surname, set())) == 1:
                    (person,) = people[surname]
                else:
                    person = cs.key(f"{SOURCE}.person", f"{spell(text)[0]}-{surname}")
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=text, lang="en", kind="official", is_preferred=True)
                    people[surname] = {person}
                options[label] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=text)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=text,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len(set(options.values()) | set(current.values())) != len(options):
                raise ValueError(f"{day}: options do not line up")

            result = cs.key(f"{SOURCE}.result_set", f"{day}:mayor")
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=method)
            count = len(next(iter(cells.values())))
            values: list[tuple[UUID | None, int, int, int, list[float] | None, int]] = []
            if ballots is not None and invalid is not None:
                valid = ballots[0] - invalid[0]
                values += [
                    (None, 3, 0, ballots[0], ballots[1], 0),
                    (None, 5, 0, invalid[0], invalid[1], 0),
                    (None, 4, 1, valid, None, 1),
                ]
                for number in range(count):
                    continuing = sum(column[number]["votes"] for column in cells.values())
                    values.append((None, 21, number + 1, valid - continuing, None, 1))
            for label, column in cells.items():
                for number, cell in enumerate(column):
                    if number == 0 or column[number - 1]["votes"]:
                        values.append((options[label], 1, number + 1, cell["votes"], cell["box"], 0))
            final = {label: column[-1]["votes"] for label, column in cells.items()}
            winner = max(final, key=lambda label: final[label])
            if 2 * final[winner] <= sum(final.values()):
                raise ValueError(f"{day}: no final-round majority")
            for label, option in options.items():
                for outcome in ("won", "elected") if label == winner else ("lost" if final[label] else "eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            for option, measure, number, votes, box, flags in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=top,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=8 if box is not None else flags,
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
