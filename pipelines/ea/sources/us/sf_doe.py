import gzip
import html
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.sf_doe"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ORIGIN = "https://www.sfelections.org/results/"
WAYBACK = "https://web.archive.org/web/{}id_/" + ORIGIN + "{}"
PUBLISHER = "San Francisco Department of Elections"
PLANS = {
    "2012": "https://data.sf.gov/api/geospatial/bsfq-aeyw?method=export&format=GeoJSON",
    "2022": "https://data.sf.gov/api/geospatial/d6x4-hefw?method=export&format=GeoJSON",
}
RECORDS = {
    "2020-11-03": ("20210628011559", "20201103/data/20201201/CVR_Export_20201201091840.zip"),
    "2022-11-08": ("20230419065651", "20221108/data/20221201/CVR_Export_20221201120428.zip"),
}
IMAGES = {
    "2015-11-03": (
        ("20151205182055", "20151103/data/20151119/20151119_ballotimage.txt"),
        ("20151122163351", "20151103/data/20151119/20151119_masterlookup.txt"),
    ),
    "2016-11-08": (
        ("20211016011754", "20161108/data/20161206/20161206_ballotimage.txt"),
        ("20211016012524", "20161108/data/20161206/20161206_masterlookup.txt"),
    ),
    "2018-06-05": (
        ("20230410112050", "20180605/data/20180627/20180627_ballotimage.txt"),
        ("20190717051423", "20180605/data/20180627/20180627_masterlookup.txt"),
    ),
    "2018-11-06:CA:san-francisco:supervisors:6": (
        ("20230423125442", "20181106/data/20181127/d6/20181127_d6_ballotimage.txt"),
        ("20190721230054", "20181106/data/20181127/20181127_masterlookup.txt"),
    ),
}
CONTESTS: dict[str, tuple[str, tuple[str, str]]] = {
    "2015-11-03:CA:san-francisco:mayor": (
        "alged:667000_2015_11_san francisco_CA_Mayor_mayor_1",
        ("20151122163411", "20151103/data/20151119/mayor/20151119_mayor.html"),
    ),
    "2015-11-03:CA:san-francisco:sheriff": (
        "alged:6075_2015_11_san francisco_CA_Sheriff_000_1",
        ("20151122163646", "20151103/data/20151119/sheriff/20151119_sheriff.html"),
    ),
    "2015-11-03:CA:san-francisco:supervisors:3": (
        "alged:667000_2015_11_san francisco_CA_City Council_3_1",
        ("20151122163405", "20151103/data/20151119/d3/20151119_d3.html"),
    ),
    "2016-11-08:CA:san-francisco:supervisors:1": (
        "alged:667000_2016_11_san francisco_CA_City Council_1_1",
        ("20170205143500", "20161108/data/20161206/d1/20161206_d1.html"),
    ),
    "2016-11-08:CA:san-francisco:supervisors:7": (
        "alged:667000_2016_11_san francisco_CA_City Council_7_1",
        ("20170205134113", "20161108/data/20161206/d7/20161206_d7.html"),
    ),
    "2016-11-08:CA:san-francisco:supervisors:9": (
        "alged:667000_2016_11_san francisco_CA_City Council_9_1",
        ("20170205142200", "20161108/data/20161206/d9/20161206_d9.html"),
    ),
    "2016-11-08:CA:san-francisco:supervisors:11": (
        "alged:667000_2016_11_san francisco_CA_City Council_11_1",
        ("20170205140104", "20161108/data/20161206/d11/20161206_d11.html"),
    ),
    "2018-06-05:CA:san-francisco:mayor": (
        "alged:667000_2018_6_san francisco_CA_Mayor_mayor_1",
        ("20190717052808", "20180605/data/20180627/mayor/20180627_mayor.html"),
    ),
    "2018-06-05:CA:san-francisco:supervisors:8": (
        "alged:6075_2018_6_san francisco_CA_County Legislature_8_1",
        ("20190718045110", "20180605/data/20180627/d8/20180627_d8.html"),
    ),
    "2018-11-06:CA:san-francisco:supervisors:2": (
        "alged:667000_2018_11_san francisco_CA_City Council_2_1",
        ("20190718042650", "20181106/data/20181127/d2/20181127_d2.html"),
    ),
    "2018-11-06:CA:san-francisco:supervisors:4": (
        "alged:667000_2018_11_san francisco_CA_City Council_4_1",
        ("20190716075318", "20181106/data/20181127/d4/20181127_d4.html"),
    ),
    "2018-11-06:CA:san-francisco:supervisors:6": (
        "alged:667000_2018_11_san francisco_CA_City Council_6_1",
        ("20190718042653", "20181106/data/20181127/d6/20181127_d6.html"),
    ),
    "2018-11-06:CA:san-francisco:supervisors:10": (
        "alged:667000_2018_11_san francisco_CA_City Council_10_1",
        ("20190718052033", "20181106/data/20181127/d10/20181127_d10.html"),
    ),
    "2019-11-05:CA:san-francisco:mayor": (
        "alged:667000_2019_11_san francisco_CA_Mayor_mayor_1",
        ("20191127231534", "20191105/data/20191125/mayor/20191125_mayor_short.xlsx"),
    ),
    "2019-11-05:CA:san-francisco:supervisors:5": (
        "alged:667000_2019_11_san francisco_CA_City Council_5_1",
        ("20191127231531", "20191105/data/20191125/d5/20191125_d5_short.xlsx"),
    ),
    "2019-11-05:CA:san-francisco:district-attorney": (
        "alged:6075_2019_11_san francisco_CA_Prosecutor_san francisco_1",
        ("20191127231537", "20191105/data/20191125/da/20191125_da_short.xlsx"),
    ),
    "2020-11-03:CA:san-francisco:supervisors:1": (
        "alged:667000_2020_11_san francisco_CA_City Council_1_1",
        ("20210627223312", "20201103/data/20201201/d1/20201201_d1_short.xml"),
    ),
    "2020-11-03:CA:san-francisco:supervisors:3": (
        "alged:667000_2020_11_san francisco_CA_City Council_3_1",
        ("20210628141209", "20201103/data/20201201/d3/20201201_d3_short.xml"),
    ),
    "2020-11-03:CA:san-francisco:supervisors:5": (
        "alged:667000_2020_11_san francisco_CA_City Council_5_1",
        ("20210628014103", "20201103/data/20201201/d5/20201201_d5_short.xml"),
    ),
    "2020-11-03:CA:san-francisco:supervisors:7": (
        "alged:667000_2020_11_san francisco_CA_City Council_7_1",
        ("20210628210831", "20201103/data/20201201/d7/20201201_d7_short.xml"),
    ),
    "2020-11-03:CA:san-francisco:supervisors:11": (
        "alged:667000_2020_11_san francisco_CA_City Council_11_1",
        ("20210628014126", "20201103/data/20201201/d11/20201201_d11_short.xml"),
    ),
    "2022-11-08:CA:san-francisco:supervisors:6": (
        "ceda:202200441",
        ("20230419065219", "20221108/data/20221201/d6/d6_short.xlsx"),
    ),
    "2022-11-08:CA:san-francisco:district-attorney": (
        "ceda:202200443",
        ("20230419065542", "20221108/data/20221201/da/da_short.xlsx"),
    ),
    "2024-11-05:CA:san-francisco:mayor": (
        "ceda:202400229",
        ("20260928065720", "20241105/data/20241203/mayor/mayor_short.pdf"),
    ),
    "2024-11-05:CA:san-francisco:supervisors:1": (
        "ceda:202400222",
        ("20241220093021", "20241105/data/20241203/d1/d1_short.pdf"),
    ),
    "2024-11-05:CA:san-francisco:supervisors:3": (
        "ceda:202400224",
        ("20260928065709", "20241105/data/20241203/d3/d3_short.pdf"),
    ),
    "2024-11-05:CA:san-francisco:supervisors:5": (
        "ceda:202400225",
        ("20250624134831", "20241105/data/20241203/d5/d5_short.pdf"),
    ),
    "2024-11-05:CA:san-francisco:supervisors:7": (
        "ceda:202400226",
        ("20260928065710", "20241105/data/20241203/d7/d7_short.pdf"),
    ),
    "2024-11-05:CA:san-francisco:supervisors:9": (
        "ceda:202400227",
        ("20260928065711", "20241105/data/20241203/d9/d9_short.pdf"),
    ),
    "2024-11-05:CA:san-francisco:supervisors:11": (
        "ceda:202400223",
        ("20260928065711", "20241105/data/20241203/d11/d11_short.pdf"),
    ),
}

OFFICIAL = (
    "The Department of Elections' ranked-choice report for the contest. Ballots exhausted in a round are the "
    "report's non-transferable total, which counts blank ballots, overvotes, and exhausted ballots together. Each "
    "candidate's value at round n is their running total; an eliminated candidate has 0 at the round of their "
    "elimination."
)
DERIVED = (
    "Tabulated by electionsarchive.com from the Department of Elections' cast vote records, precinct by precinct. A "
    "ballot exhausts at an overvote, a skipped ranking or a write-in for someone who did not qualify is passed over, "
    "a repeated candidate counts once, and blank ballots are exhausted from the first round, as San Francisco's rules "
    "have them. Each round drops the candidates the official count dropped. Summed over {}, every round reproduces "
    "the Department of Elections' count exactly."
)


def fetch() -> None:
    downloads = [(plan, ".geojson") for plan in PLANS.values()]
    for stamp, path in [
        *(report for _, report in CONTESTS.values()),
        *RECORDS.values(),
        *(file for files in IMAGES.values() for file in files),
    ]:
        downloads.append((WAYBACK.format(stamp, path), "." + path.rsplit(".", 1)[1]))
    for url, suffix in downloads:
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, url, suffix=suffix)
                break
            except httpx.HTTPError:
                time.sleep(10 * 2**attempt)
        else:
            raw.fetch(SOURCE, url, suffix=suffix)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    official: dict[str, list[dict[str | None, int]]] = {}
    for key, (_, (stamp, path)) in CONTESTS.items():
        file = raw.cached(SOURCE, WAYBACK.format(stamp, path)).path
        lines: list[tuple[str, list[int]]] = []
        if path.endswith(".html"):
            spare = {}
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", file.read_bytes().decode("utf-8"), re.DOTALL | re.IGNORECASE):
                cells = [
                    html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                    for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
                ]
                values = [int(value) for value in cells[1::3] if re.fullmatch(r"-?\d+", value)]
                if not cells or not cells[0] or not values or cells[0] in ("Continuing Ballots", "TOTAL"):
                    continue
                if cells[0] in ("Exhausted by Over Votes", "Under Votes", "Exhausted Ballots"):
                    spare[cells[0]] = values
                elif not cells[0].startswith("WRITE-IN") or any(values):
                    lines.append((re.sub(r"^WRITE-IN\s+-?\s*", "", cells[0]), values))
            lines.append(("Non Transferable Total", [sum(values) for values in zip(*spare.values(), strict=True)]))
        elif path.endswith(".xml"):
            root = ET.parse(file).getroot()
            space = root.tag[1:].split("}")[0]
            for group in root.iter(f"{{{space}}}choiceGroup"):
                name = next(str(element.get("choiceName")) for element in group.iter() if element.get("choiceName"))
                values = []
                for step in group.iter(f"{{{space}}}roundGroup"):
                    values.append(round(float(next(str(e.get("votes")) for e in step.iter() if e.get("votes")))))
                    values.append(
                        round(
                            float(next((str(e.get("voteTransfer")) for e in step.iter() if e.get("voteTransfer")), "0"))
                        )
                    )
                lines.append((name, values))
            lines.append(
                (
                    "Non Transferable Total",
                    [
                        round(float(str(e.get("nonTransferableVotes"))))
                        for e in root.iter()
                        if e.get("nonTransferableVotes")
                    ],
                )
            )
        else:
            if path.endswith(".xlsx"):
                workbook = fastexcel.read_excel(file)
                cells = [
                    [cell.strip() for cell in row if cell not in (None, "")]
                    for name in workbook.sheet_names
                    for row in workbook.load_sheet(name, header_row=None, dtypes="string").to_polars().iter_rows()
                ]
                start = next(index for index, row in enumerate(cells) if row and row[0] == "Candidate")
                rows = [(row[0], row[1:]) for row in cells[start + 1 :] if row]
            else:
                text = subprocess.run(
                    ["pdftotext", "-layout", str(file), "-"], capture_output=True, text=True, check=True
                ).stdout
                pages = []
                for page in text.split("\f"):
                    body = (
                        re.split(r"\n[ \t]*San Francisco[ \t]*\n", page)[-1] if "Tabulation Options" in page else page
                    )
                    found = []
                    for line in body.splitlines():
                        if re.match(r"\s*Page: \d+ / \d+", line):
                            continue
                        match = re.match(r"^\s*()((?:-?[\d,]+(?:\.\d+%)?\s*)+)$", line) or re.match(
                            r"^\s*(\S.*?\S)\s{2,}((?:-?[\d,]+(?:\.\d+%)?\s*)+)$", line
                        )
                        if match:
                            found.append((match[1].strip(), match[2].split()))
                    pages.append((bool(re.search(r"Round \d+", page)), found))
                width = sum(headed for headed, _ in pages)
                columns: dict[int, list[tuple[str, list[str]]]] = {}
                for index, (_, found) in enumerate(pages):
                    columns.setdefault(index % width, []).extend(found)
                rows = []
                for index, (label, values) in enumerate(columns[0]):
                    if any(columns[group][index][0] for group in range(1, width)):
                        raise ValueError(f"{key}: row {index} of the report does not line up across pages")
                    rows.append((label, [value for group in range(width) for value in columns[group][index][1]]))
                rows = [(label, values) for label, values in rows if label]
            for label, values in rows:
                numbers = [
                    round(float(value.replace(",", "")))
                    for value in values
                    if re.fullmatch(r"-?[\d,]+" if path.endswith(".pdf") else r"-?\d+(?:\.\d+)?", value)
                ]
                if numbers:
                    lines.append((label, numbers))
        totals = dict(lines)["Non Transferable Total"]
        candidates = {
            label: values[:: 3 if len(values) == 3 * len(totals) else 1 if len(values) == len(totals) else 2]
            for label, values in lines
            if label not in ("Continuing Ballots Total", "Blanks", "Exhausted", "Overvotes", "Remainder Points")
            and label != "Non Transferable Total"
        }
        if any(len(votes) != len(totals) for votes in candidates.values()):
            raise ValueError(f"{key}: the report's rows have different numbers of rounds")
        rounds = [
            {
                **{label: votes[number] for label, votes in candidates.items() if number == 0 or votes[number] > 0},
                None: totals[number],
            }
            for number in range(len(totals))
        ]
        if len({sum(tally.values()) for tally in rounds}) != 1:
            raise ValueError(f"{key}: the report's rounds do not add up to one ballot total")
        official[key] = rounds
    (PARSED / "official.json").write_text(
        json.dumps(
            {
                key: [[[label, votes] for label, votes in tally.items()] for tally in rounds]
                for key, rounds in official.items()
            }
        )
    )

    frames = []
    cast: dict[str, list[tuple[str, list[set[str]]]]] = {}
    for day, records in RECORDS.items():
        with zipfile.ZipFile(raw.cached(SOURCE, WAYBACK.format(*records)).path) as archive:
            manifest = {
                name: json.loads(archive.read(f"{name}Manifest.json"))["List"]
                for name in ("Contest", "Candidate", "PrecinctPortion")
            }
            portions = {
                portion["Id"]: re.findall(r"\d{4}", portion["Description"])[0]
                for portion in manifest["PrecinctPortion"]
            }
            wanted = {}
            for key in CONTESTS:
                if not key.startswith(day):
                    continue
                office = key.split(":", 3)[3]
                pattern = (
                    rf"(?:MEMBER, )?BOARD OF SUPERVISORS,? DISTRICT {office.split(':')[1]}"
                    if office.startswith("supervisors")
                    else office.replace("-", " ").upper()
                )
                wanted[next(c["Id"] for c in manifest["Contest"] if re.fullmatch(pattern, c["Description"]))] = key
            names = {candidate["Id"]: candidate["Description"] for candidate in manifest["Candidate"]}
            cast.update({key: [] for key in wanted.values()})
            for member in archive.namelist():
                if not member.startswith("CvrExport"):
                    continue
                for session in json.loads(archive.read(member))["Sessions"]:
                    side = session.get("Modified") or session["Original"]
                    for card in side["Cards"]:
                        for contest in card["Contests"]:
                            if contest["Id"] not in wanted:
                                continue
                            ranks: dict[int, set[str]] = {}
                            for mark in contest["Marks"]:
                                if not mark["IsAmbiguous"]:
                                    ranks.setdefault(mark["Rank"], set()).add(names[mark["CandidateId"]])
                            cast[wanted[contest["Id"]]].append(
                                (
                                    portions[side["PrecinctPortionId"]],
                                    [ranks.get(rank, set()) for rank in range(1, max(ranks, default=0) + 1)],
                                )
                            )
    for scope, (image, lookup) in IMAGES.items():
        labels, contests, precincts = {}, {}, {}
        for line in raw.cached(SOURCE, WAYBACK.format(*lookup)).path.read_text(encoding="utf-8").splitlines():
            kind, ident, description = line[:10].strip(), line[10:17], line[17:67].strip()
            if kind == "Candidate":
                labels[ident] = re.sub(r"^WRITE-IN\s+-?\s*", "", description)
            elif kind == "Contest":
                contests[description] = ident
            elif kind == "Precinct":
                precincts[ident] = "/".join(sorted(re.findall(r"\d{4}", description)))
        wanted = {}
        for key in CONTESTS:
            if key.startswith(scope):
                office = key.split(":", 3)[3]
                wanted[
                    contests[
                        f"Board of Supervisors, District {office.split(':')[1]}"
                        if office.startswith("supervisors")
                        else office.title()
                    ]
                ] = key
        data = raw.cached(SOURCE, WAYBACK.format(*image)).path.read_bytes()
        records: dict[tuple[str, str], tuple[str, dict[int, set[str]]]] = {}
        for line in (gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data).decode("ascii").splitlines():
            if line[:7] not in wanted:
                continue
            _, ranks = records.setdefault((line[:7], line[7:16]), (precincts[line[26:33]], {}))
            ranks[int(line[33:36])] = (
                {"over", "votes"}
                if line[43] == "1"
                else set()
                if line[44] == "1" or line[36:43] == "0000000"
                else {labels[line[36:43]]}
            )
        for (contest, _), (precinct, ranks) in records.items():
            cast.setdefault(wanted[contest], []).append((precinct, [ranks[rank] for rank in sorted(ranks)]))
    for key, ballots in cast.items():
        rounds = official[key]
        continuing = [{label for label in tally if label is not None} for tally in rounds]
        counts: dict[tuple[str, int, str | None], int] = {}
        for unit, ranking in ballots:
            for number, standing in enumerate(continuing, 1):
                top = None
                for marks in ranking:
                    if len(marks) > 1:
                        break
                    if not marks <= continuing[0]:
                        continue
                    (choice,) = marks or {None}
                    if choice in standing:
                        top = choice
                        break
                counts[(unit, number, top)] = counts.get((unit, number, top), 0) + 1
        units = sorted({unit for unit, _, _ in counts})
        tally = pl.DataFrame(
            [
                *((unit, number, label, votes) for (unit, number, label), votes in counts.items()),
                *(
                    (unit, number, label, 0)
                    for number in range(2, len(continuing) + 1)
                    for label in sorted(continuing[number - 2] - continuing[number - 1])
                    for unit in units
                ),
            ],
            schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
            orient="row",
        )
        sums = tally.group_by("round", "label").agg(pl.col("votes").sum())
        for number, expected in enumerate(rounds, 1):
            found = {label: votes for _, label, votes in sums.filter(pl.col("round") == number).iter_rows() if votes}
            if found != {label: votes for label, votes in expected.items() if votes}:
                raise ValueError(f"{key} round {number}: the records give {found}, the official count {expected}")
        frames.append(tally.with_columns(key=pl.lit(key)))
    pl.concat(frames).write_parquet(PARSED / "tallies.parquet")

    rows = []
    with connect() as conn:
        for vintage, plan in PLANS.items():
            features = json.loads(raw.cached(SOURCE, plan).path.read_text())["features"]
            rows.extend(
                (vintage, *row)
                for row in conn.execute(
                    """select f.code, encode(st_asewkb(s.geom), 'hex'),
                         encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
                       from unnest(%s::text[], %s::text[]) as f(code, geojson)
                       cross join lateral (select st_multi(st_collectionextract(st_makevalid(
                         st_setsrid(st_geomfromgeojson(f.geojson), 4326)), 3)) as geom) s""",
                    [
                        [str(feature["properties"][f"prec_{vintage}"]) for feature in features],
                        [json.dumps(feature["geometry"]) for feature in features],
                    ],
                ).fetchall()
            )
    pl.DataFrame(rows, schema=["plan", "code", "geom", "point"], orient="row").write_parquet(
        PARSED / "precincts.parquet"
    )


def emit() -> Path:
    official = {
        key: [{label: votes for label, votes in tally} for tally in rounds]
        for key, rounds in json.loads((PARSED / "official.json").read_text()).items()
    }
    tallies = pl.read_parquet(PARSED / "tallies.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="San Francisco's ranked-choice contests for mayor, district attorney, the Board of Supervisors, "
            "and sheriff that went past round 1, 2015 to 2024, with each round by precinct from the cast vote records "
            "and ballot images",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        county = cs.area("us.county", "06075")
        plans = {}
        for vintage, url in PLANS.items():
            plans[vintage] = cs.source("City and County of San Francisco", f"Election Precincts, {vintage}")
            cs.add(
                "source",
                id=plans[vintage],
                kind="official_body",
                publisher="City and County of San Francisco",
                title=f"Election Precincts, {vintage}",
                url="https://data.sf.gov/d/" + url.split("/")[-1].split("?")[0],
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), plans[vintage])
        shapes = {
            (vintage, code): (geom, point)
            for vintage, code, geom, point in pl.read_parquet(PARSED / "precincts.parquet").iter_rows()
        }
        precincts: dict[str, dict[str, list[UUID]]] = {}
        for day in ("2016-11-08", "2020-11-03"):
            for number, unit in conn.execute(
                """select substring(n.text from '^Precinct (\\d{4}), '), ru.id from reporting_unit ru
                   join name n on n.entity_id = ru.area_id and n.is_preferred
                   join area_parent p on p.area_id = ru.area_id
                   where ru.scheme_code = 'us.precinct' and ru.code ~ %s and p.parent_id = %s""",
                [f"^{day[:4]}:CA:[0-9]+$", county],
            ).fetchall():
                precincts.setdefault(day, {}).setdefault(number, []).append(unit)
        for row in conn.execute(
            """select substr(split_part(code, ':', 5), 6, 4), id, polity_id, code, parent_id, counted_with_id,
                 encode(st_asewkb(location), 'hex'), kind, valid_from, valid_to
               from reporting_unit where scheme_code = 'us.precinct' and code like '2022:CA:06075:%%'"""
        ).fetchall():
            number, unit, polity, code = row[:4]
            precincts.setdefault("2022-11-08", {}).setdefault(number, []).append(unit)
            area = cs.area("us.precinct", code)
            cs.add("area", id=area, scheme_code="us.precinct")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=county)
            name(area, f"Precinct {number}, San Francisco County")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=date(2022, 11, 8),
                valid_to=date(2022, 11, 9),
                geom=shapes["2022", number][0],
                label_point=shapes["2022", number][1],
                quality="official",
                source_id=plans["2022"],
            )
            cs.add(
                "reporting_unit",
                **dict(
                    zip(
                        (
                            "id",
                            "polity_id",
                            "code",
                            "parent_id",
                            "counted_with_id",
                            "location",
                            "kind",
                            "valid_from",
                            "valid_to",
                        ),
                        (unit, polity, code, *row[4:]),
                        strict=True,
                    )
                ),
                scheme_code="us.precinct",
                area_relation="equals",
                area_id=area,
            )
        (polity,) = conn.execute("select polity_id from polity_territory where area_id = %s", [county]).fetchone() or (
            None,
        )
        if polity is None:
            raise ValueError("San Francisco County must already be a polity")
        older = tallies.filter(pl.col("key").str.slice(0, 10).is_in(["2015-11-03", "2018-06-05", "2018-11-06"]))
        for number in sorted(set(older["unit"])):
            members = number.split("/")
            area = cs.area("us.precinct", "2012:CA:06075:" + "-".join(members))
            cs.add("area", id=area, scheme_code="us.precinct")
            cs.add("area_code", area_id=area, code="2012:CA:06075:" + "-".join(members), valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=county)
            name(area, f"Precinct {number}, San Francisco County")
            if all(("2012", member) in shapes for member in members):
                geom, point = (
                    shapes["2012", members[0]]
                    if len(members) == 1
                    else conn.execute(
                        """select encode(st_asewkb(g), 'hex'), encode(st_asewkb(st_pointonsurface(g)), 'hex')
                           from (select st_multi(st_collectionextract(st_makevalid(st_union(
                             st_geomfromewkb(decode(h, 'hex')))), 3)) as g from unnest(%s::text[]) as h) u""",
                        [[shapes["2012", member][0] for member in members]],
                    ).fetchone()
                    or (None, None)
                )
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=date(2015, 11, 3),
                    valid_to=date(2018, 11, 7),
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=plans["2012"],
                )
            for day in sorted(set(older.filter(pl.col("unit") == number)["key"].str.slice(0, 10))):
                code = f"{day}:CA:06075:" + "-".join(members)
                unit = cs.unit("us.precinct", code)
                precincts.setdefault(day, {})[number] = [unit]
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="us.precinct",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=date.fromisoformat(day),
                    valid_to=date.fromisoformat(day) + timedelta(days=1),
                )
        for day in ("2015-11-03", "2018-06-05"):
            cs.add(
                "boundary_set",
                scheme_code="us.precinct",
                vintage=day,
                valid_from=date.fromisoformat(day),
                valid_to=date.fromisoformat(day) + timedelta(days=1),
                as_of=date.fromisoformat(day),
            )
        people: dict[tuple[str, str], set[tuple[UUID, date, bool]]] = {}
        for text, person, held, city in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id, e.date_from,
                 coalesce(x.value like 'alged:667000%%', false) from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               left join external_id x on x.entity_id = k.id and x.scheme = 'us.contest'
               join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
               where t.area_id in (%s, %s)""",
            [county, cs.area("us.place", "0667000")],
        ).fetchall():
            words = [word for word in re.findall(r"[\w'-]+", text.casefold()) if word not in ("jr", "sr")]
            people.setdefault((words[0], words[-1]), set()).add((person, held, city))

        irv = cs.key("us.electoral_system", "irv")
        if irv in cs.minted:
            raise ValueError("the IRV electoral system must already exist")
        for key, (existing, (stamp, path)) in CONTESTS.items():
            day = date.fromisoformat(key[:10])
            rounds = official[key]
            contest = cs.key("us.contest", existing)
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
            row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
            if contest in cs.minted or row is None:
                raise ValueError(f"{key}: {existing} must already exist")
            contest_row = dict(zip(columns, row, strict=True))
            cs.add("contest", id=contest, **contest_row, electoral_system_id=irv)
            election, district = contest_row["election_id"], contest_row["district_area_id"]
            previous = cs.key(f"us.{existing.split(':')[0]}.result_set", existing)
            if previous in cs.minted:
                raise ValueError(f"{key}: {existing}'s result set must already exist")
            tops = conn.execute(
                """select distinct u.id from result_value v join result_set r on r.k = v.result_set_k
                   join reporting_unit u on u.k = v.unit_k where r.id = %s""",
                [previous],
            ).fetchall()
            if len(tops) != 1:
                raise ValueError(f"{key}: {existing}'s result set has {len(tops)} reporting units")
            top = tops[0][0]
            office = key.split(":", 3)[3]
            title = (
                f"{day:%B} {day.day}, {day.year} election: "
                + (
                    f"Board of Supervisors, District {office.split(':')[1]}"
                    if office.startswith("supervisors")
                    else office.replace("-", " ").title()
                )
                + ", ranked-choice report"
            )
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=ORIGIN + path,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, path)), source)

            current = conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'", [contest]
            ).fetchall()
            options: dict[str, UUID] = {}
            for label in (label for label in rounds[0] if label is not None):
                if label == "WRITE-IN":
                    found = [
                        option
                        for (option,) in conn.execute(
                            "select id from contest_option where contest_id = %s and kind = 'write_in'", [contest]
                        ).fetchall()
                    ]
                    if len(found) > 1:
                        raise ValueError(f"{key}: {len(found)} write-in options")
                    options[label] = found[0] if found else cs.key("us.option", f"{key}:write_in:scattered")
                    if not found:
                        cs.add(
                            "contest_option",
                            id=options[label],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Write-in",
                        )
                    continue
                words = [word for word in re.findall(r"[\w'-]+", label.casefold()) if word not in ("jr", "sr")]
                found = [option for option, text in current if text.casefold().split()[-1] == words[-1]]
                if len(found) == 1:
                    options[label] = found[0]
                    continue
                if found:
                    raise ValueError(f"{key}: {len(found)} options for {label}")
                display = label.title().replace("''", '"')
                known = people.get((words[0], words[-1]), set())
                matches = {person for person, _, _ in known}
                for preferred in (
                    {person for person, held, _ in known if held == day},
                    {person for person, _, city in known if city},
                ):
                    if len(preferred) == 1:
                        matches = preferred
                        break
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} people for {label}")
                if matches:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(display))
                    cs.add("person", id=person)
                    name(person, display)
                options[label] = cs.key("us.option", f"{key}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=display)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=display,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len(set(options.values())) != len(options):
                raise ValueError(f"{key}: two candidates share an option")

            for values in conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                     supersedes_id from result_set where id = %s""",
                [previous],
            ).fetchall():
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            (
                                "id",
                                "election_id",
                                "contest_id",
                                "stage",
                                "kind",
                                "status",
                                "source_id",
                                "as_of",
                                "completeness",
                                "supersedes_id",
                            ),
                            values,
                            strict=True,
                        )
                    ),
                    is_default=False,
                )
            result = cs.key(f"{SOURCE}.result_set", key)
            final = rounds[-1]
            winner = max((label for label in final if label is not None), key=lambda label: final[label])
            for label, option in options.items():
                for kind in ("won", "elected") if label == winner else ("lost" if label in final else "eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=kind)
            sets = [
                (
                    result,
                    True,
                    OFFICIAL,
                    pl.DataFrame(
                        [
                            ("top", number, label, tally.get(label, 0))
                            for number, tally in enumerate(rounds, 1)
                            for label in rounds[0]
                            if label in tally or label in rounds[number - 2]
                        ],
                        schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
                        orient="row",
                    ),
                    0,
                )
            ]
            units = {"top": str(top)}
            rows = tallies.filter(pl.col("key") == key)
            if not rows.is_empty():
                for number in rows["unit"].unique():
                    found = precincts[key[:10]].get(number.split("/")[0] if key.startswith("2016") else number, [])
                    if len(found) != 1:
                        raise ValueError(f"{key}: {len(found)} reporting units for precinct {number}")
                    units[number] = str(found[0])
                if office.startswith("supervisors"):
                    cs.frame(
                        "reporting_unit_area",
                        pl.DataFrame(
                            {
                                "unit_id": [units[number] for number in rows["unit"].unique().sort()],
                                "area_id": str(district),
                                "valid_from": day,
                                "valid_to": day + timedelta(days=1),
                            }
                        ),
                    )
                sets.append(
                    (
                        cs.key(f"{SOURCE}.result_set", f"{key}:precincts"),
                        False,
                        DERIVED.format(
                            "the district"
                            if office.startswith("supervisors")
                            else "the city"
                            if office == "mayor"
                            else "the county"
                        ),
                        rows.select("unit", "round", "label", "votes"),
                        1,
                    )
                )

            for result_set, default, note, values, flags in sets:
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                if result_set not in cs.minted:
                    continue
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                unknown = set(values["label"].drop_nulls()) - set(options)
                if unknown:
                    raise ValueError(f"{key}: no options for {unknown}")
                frame = values.with_columns(unit_id=pl.col("unit").replace_strict(units))
                first = frame.filter(pl.col("round") == 1)
                frame = pl.concat(
                    [
                        frame,
                        first.group_by("unit", "unit_id")
                        .agg(pl.col("votes").sum())
                        .select(
                            "unit", round=pl.lit(0, pl.Int16), label=pl.lit("ballots"), votes="votes", unit_id="unit_id"
                        ),
                        first.filter(pl.col("label").is_not_null())
                        .group_by("unit", "unit_id")
                        .agg(pl.col("votes").sum())
                        .select(
                            "unit", round=pl.lit(1, pl.Int16), label=pl.lit("valid"), votes="votes", unit_id="unit_id"
                        ),
                    ]
                )
                cs.frame(
                    "result_value",
                    frame.select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id="unit_id",
                        option_id=pl.col("label").replace_strict(
                            {label: str(option) for label, option in options.items()}, default=None
                        ),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.when(pl.col("label").is_null())
                        .then(21)
                        .otherwise(pl.col("label").replace_strict({"ballots": 3, "valid": 4}, default=1))
                        .cast(pl.Int16),
                        count_round=pl.col("round").cast(pl.Int16),
                        flags=pl.lit(flags, pl.Int16),
                        value="votes",
                    ),
                )
        return cs.write()
