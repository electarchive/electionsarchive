import csv
import difflib
import io
import json
import re
import subprocess
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.mpls_evs"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of Minneapolis Elections & Voter Services"
BASE = "https://vote.minneapolismn.gov/media/-www-content-assets/documents/archive/elections/"
PLANS = {
    "2009": "2008-11-04",
    "2013": "2012-11-06",
    "2017": "2016",
    "2021": "2020",
    "2023": "2022",
    "2025": "2024-11-05",
}
CONTESTS: dict[str, tuple[str | None, str, str | None]] = {
    **{
        f"2009-11-03:MN:minneapolis:city-council:{ward}": (
            f"alged:2743000_2009_11_minneapolis_MN_City Council_{ward}_1",
            f"2009-Council-Ward-{ward:02}-Final-RCV.xls",
            f"2009-Council-Ward-{ward}-CVR.xls",
        )
        for ward in (4, 5)
    },
    "2013-11-05:MN:minneapolis:mayor": (
        "alged:2743000_2013_11_minneapolis_MN_Mayor_mayor_1",
        "2013-Mayor-RCV-Tabulation-Summary.xlsx",
        "2013-Mayor-CVR.xlsx",
    ),
    **{
        f"2013-11-05:MN:minneapolis:city-council:{ward}": (
            f"alged:2743000_2013_11_minneapolis_MN_City Council_{ward}_1",
            f"2013-City-Council-Ward{ward}-Final-RCV.xlsx",
            f"2013-Council-Ward-{ward}-CVR.xlsx",
        )
        for ward in (5, 9, 13)
    },
    "2017-11-07:MN:minneapolis:mayor": (
        "alged:2743000_2017_11_minneapolis_MN_Mayor_mayor_1",
        "2017-Mayor-Results-Summary.pdf",
        "2017-Mayor-Ballot-Records.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:1": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_1_1",
        "2017-Ward-1-Results-Summary.pdf",
        "2017-Ward-1-Ballot-Records.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:3": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_3_1",
        "2017-Ward-3-Results-Summary.pdf",
        "2017-Ward-3-Ballot-Records.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:4": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_4_1",
        "2017-Ward-4-Results-Summary.pdf",
        "2017-Ward-4-Ballot-Records.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:5": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_5_1",
        "2017-Ward-5-RCV-Tabulation-Summary.pdf",
        "2017-Council-Ward-5-CVR.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:9": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_9_1",
        "2017-Ward-9-Race-Results-Summary.pdf",
        "2017-Ward-9-Ballot-Records-.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:11": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_11_1",
        "2017-Ward-11-Results-Summary.pdf",
        "2017-Ward-11-Ballot-Records.csv",
    ),
    "2021-11-02:MN:minneapolis:mayor": (
        "alged:2743000_2021_11_minneapolis_MN_Mayor_mayor_1",
        "2021-Mayor-Round-Results-Summary.pdf",
        "2021-Mayor-Cast-Vote-Record.csv",
    ),
    **{
        f"2021-11-02:MN:minneapolis:city-council:{ward}": (
            f"alged:2743000_2021_11_minneapolis_MN_City Council_{ward}_1",
            f"2021-Council-Ward-{ward}-Round-Results-Summary.pdf",
            f"2021-Ward-{ward}-Cast-Vote-Record.csv",
        )
        for ward in (1, 2, 3, 5, 10)
    },
    "2009-11-03:MN:minneapolis:park-board:1": (None, "2009-MPRB-District-01-Final-RCV.xls", None),
    **{
        f"2009-11-03:MN:minneapolis:park-board:{district}": (
            None,
            f"2009-MPRB-District-{district:02}-Final-RCV.xls",
            f"2009-MPRB-District-{district}-CVR.xls",
        )
        for district in range(2, 7)
    },
    "2009-11-03:MN:minneapolis:park-board:at-large": (
        None,
        "2009-MPRB-At-Large-Final-RCV.xls",
        "2009-MPRB-At-Large-CVR.xls",
    ),
    "2009-11-03:MN:minneapolis:board-of-estimate-and-taxation": (
        None,
        "2009-BET-Final-RCV.xls",
        "2009-BET-At-Large-CVR.xls",
    ),
    "2013-11-05:MN:minneapolis:park-board:at-large": (
        None,
        "2013-Park-Board-At-Large-Final-RCV.xlsx",
        "2013-Park-At-Large-CVR.xlsx",
    ),
    "2017-11-07:MN:minneapolis:board-of-estimate-and-taxation": (
        None,
        "2017-Board-of-Estimate-and-Taxation-Results-Summary.pdf",
        "2017-Board-of-Estimate-and-Taxation-Ballot-Records.csv",
    ),
    "2017-11-07:MN:minneapolis:park-board:1": (
        None,
        "2017-Park-Board-District-1-Results-Summary.pdf",
        "2017-Park-Board-District-1-Ballot-Records.csv",
    ),
    **{
        f"2017-11-07:MN:minneapolis:park-board:{district}": (
            None,
            f"2017-Park-District-{district}-RCV-Tabulation-Summary.pdf",
            f"2017-Park-District-{district}-CVR.csv",
        )
        for district in (3, 6)
    },
    "2017-11-07:MN:minneapolis:park-board:at-large": (
        None,
        "2017-Park-Board-At-Large-Race-Results-Summary.pdf",
        "2017-Park-Board-At-Large-Ballot-Records.csv",
    ),
    "2021-11-02:MN:minneapolis:board-of-estimate-and-taxation": (
        None,
        "2021-BET-Round-Results-Summary.pdf",
        "2021-BET-Cast-Vote-Record.csv",
    ),
    **{
        f"2021-11-02:MN:minneapolis:park-board:{district}": (
            None,
            f"2021-Park-Board-District-{district}-Round-Results-Summary.pdf",
            f"2021-Park-{district}-Cast-Vote-Record.csv",
        )
        for district in range(2, 7)
    },
    "2021-11-02:MN:minneapolis:park-board:at-large": (
        None,
        "2021-Park-Board-At-Large-Round-Results-Summary.pdf",
        "2021-Park-AL-Cast-Vote-Record.csv",
    ),
    **{
        f"2023-11-07:MN:minneapolis:city-council:{ward}": (
            None,
            f"2023-Ward-{ward}-Summary-Sheet.pdf",
            f"2023-Ward-{ward}-Cast-Vote-Record.csv",
        )
        for ward in (6, 7, 8)
    },
    "2025-11-04:MN:minneapolis:mayor": (
        None,
        "2025-Mayor-Round-Results-Summary.pdf",
        "2025-Mayor-Cast-Vote-Record.csv",
    ),
    "2025-11-04:MN:minneapolis:city-council:5": (
        None,
        "2025-Ward-5-RCV-Tabulation-Summary.pdf",
        "2025-Ward-5-Cast-Vote-Record.csv",
    ),
    **{
        f"2025-11-04:MN:minneapolis:park-board:{district}": (
            None,
            f"2025-Park-District-{district}-RCV-Tabulation-Summary.pdf",
            f"2025-Park-District-{district}-CVR.csv",
        )
        for district in (1, 4, 5)
    },
    "2025-11-04:MN:minneapolis:park-board:at-large": (
        None,
        "2025-Park-At-Large-RCV-Tabulation-Summary.pdf",
        "2025-Park-At-Large-CVR.csv",
    ),
    "2025-11-04:MN:minneapolis:board-of-estimate-and-taxation": (
        None,
        "2025-Board-of-Estimate-and-Taxation-RCV-Tabulation-Summary.pdf",
        "2025-BET-Cast-Vote-Record.csv",
    ),
}
SKIPPED = {"undervote", "xxx", ""}

OFFICIAL = (
    "Elections & Voter Services' ranked-choice tabulation summary statement for the contest. Minneapolis defeats "
    "every candidate who can no longer win in one round, so a round can drop several. Each candidate's value at "
    "round n is their running total; an eliminated candidate has 0 at the round of their elimination. Ballots "
    "exhausted in a round are the statement's exhausted ballots; the statement leaves out undervotes, defective "
    "ballots, and spoiled ballots."
)
SURPLUS = (
    " In contests for more than one seat, a candidate who reaches the threshold is elected. In later rounds, their "
    "surplus is moved: every ballot counting for them passes to its next continuing choice at the surplus's share of "
    "its current value, truncated to four decimal places. The rest of the value stays with the elected candidate. All "
    "values carry four decimal places."
)
DERIVED = (
    "Tabulated by electionsarchive.com from Elections & Voter Services' cast vote record, which includes each ballot's "
    "three rankings and its precinct. Following Minneapolis rules, skipped, repeated, or overvoted rankings are passed "
    "over, and ballots with no countable ranking are excluded. Each round drops the candidates the official count "
    "dropped and moves the surpluses using the same truncated fractions. When summed over the precincts, every round "
    "reproduces the tabulation summary statement exactly."
)
BOUNDARY = "The archive has no boundary for this district in 2009; the results are shown for the district as a whole."
BALLOT = {
    "city-council:4": {
        "Cermak": "Grant Cermak",
        "Harcus": "Marcus Harcus",
        "Johnson": "Barbara 'Barb' Johnson",
        "Parker": "Troy Parker",
    },
    "city-council:5": {
        "Chism": "Lennie Chism",
        "Johnson Lee": "Natalie Johnson Lee",
        "McKnight": "Kenya McKnight",
        "Samuels": "Don Samuels",
        "Smithrud": "Roger Smithrud",
    },
    "park-board:1": {"Kunza": "Bernie Kunza", "Malone": "John Malone", "Wielinski": "Liz Wielinski"},
    "park-board:2": {"Guest": "Michael Guest", "Olson": "Jon Olson"},
    "park-board:3": {"Vreeland": "Scott Vreeland", "Wendorf": "Mike Wendorf"},
    "park-board:4": {"Tabb": "Anita Tabb"},
    "park-board:5": {
        "Barland": "Steve Barland",
        "Kummer": "Carol Kummer",
        "Looney": "McLain Looney",
        "Peterson": "Dan Peterson",
        "Stone": "Jason Stone",
    },
    "park-board:6": {"Bourn": "Brad Bourn", "Forney": "Meg Forney", "Hanvik": "Geneva Hanvik", "Jecha": "Steven Jecha"},
    "park-board:at-large": {
        "Bernard": "Nancy Bernard",
        "Butler": "John Butler",
        "Erwin": "John Erwin",
        "Fine": "Bob Fine",
        "Merrill Anderson": "Mary Merrill Anderson",
        "Nordyke": "Tom Nordyke",
        "Wahlstedt": "David Wahlstedt",
        "Young": "Annie Young",
    },
    "board-of-estimate-and-taxation": {
        "Becker": "Carol J. Becker",
        "Martens": "Michael Martens",
        "Swartwood": "James Elliot Swartwood",
        "Townsend": "DeWayne Townsend",
        "Wheeler": "David B. Wheeler",
        "Willkie": "Phil Willkie",
    },
}
REDONE = ("2009-11-03:MN:minneapolis:city-council:4", "2009-11-03:MN:minneapolis:city-council:5")
BODIES = {
    "park-board": "Minneapolis Park and Recreation Board",
    "board-of-estimate-and-taxation": "Minneapolis Board of Estimate and Taxation",
}


def fetch() -> None:
    for name in [file for _, *files in CONTESTS.values() for file in files if file is not None]:
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, BASE + name, suffix="." + name.rsplit(".", 1)[1])
                break
            except httpx.HTTPError:
                time.sleep(10 * 2**attempt)
        else:
            raw.fetch(SOURCE, BASE + name, suffix="." + name.rsplit(".", 1)[1])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    official: dict[str, tuple[int, list[dict[str | None, float]]]] = {}
    frames = []
    for key, (existing, summary, record) in CONTESTS.items():
        path = raw.cached(SOURCE, BASE + summary).path
        table: list[tuple[str, list[str]]] = []
        if summary.endswith(".pdf"):
            lines = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
            ).stdout.splitlines()
            text = " ".join(lines)
            start = next(index for index, line in enumerate(lines) if re.match(r"^\s*Candidate\s{2,}", line))
            head: list[str] = []
            for line in lines[start + 1 :]:
                if line.strip().startswith("TOTAL"):
                    break
                match = re.match(r"^(.*?)\s*((?:\s+(?:-?[\d,]+(?:\.\d+)?%?|-))+)\s*$", line)
                if re.fullmatch(r"\s*Exhausted\s+-\s*", line):
                    table.append(("Exhausted", ["-"]))
                    continue
                if match is None or not re.search(r"\d", match[2]):
                    if line.strip() and not line.strip().startswith("BALANCED"):
                        head.append(line.strip())
                    continue
                label = re.sub(r"(?<=\w)- (?=\w)", "-", " ".join([*head, match[1].strip()]).strip())
                head = []
                table.append((label, [token for token in match[2].split() if not token.endswith("%")]))
            if head:
                raise ValueError(f"{key}: unmatched lines {head}")
        else:
            workbook = fastexcel.read_excel(str(path))
            sheet = (
                "Round Results Summary" if "Round Results Summary" in workbook.sheet_names else workbook.sheet_names[0]
            )
            rows = [
                ["" if cell is None else str(cell).strip() for cell in row]
                for row in workbook.load_sheet(sheet, header_row=None, dtypes="string").to_polars().iter_rows()
            ]
            text = " ".join(" ".join(row) for row in rows)
            start, column = next(
                (index, row.index("Candidate")) for index, row in enumerate(rows) if "Candidate" in row
            )
            for row in rows[start + 1 :]:
                if row[column] == "TOTAL":
                    break
                if row[column]:
                    table.append((row[column], row[column + 1 : -1]))
        rounds: list[dict[str | None, float]] = []
        for label, tokens in table:
            values = [tokens[0], *tokens[2::2]]
            for number, token in enumerate(values):
                if number and token in ("-", ""):
                    continue
                while len(rounds) <= number:
                    rounds.append({})
                votes = 0 if token in ("-", "") else float(token.replace(",", ""))
                rounds[number][None if label == "Exhausted" else label] = votes
        rounds = [
            {
                label: votes
                for label, votes in tally.items()
                if number == 0 or votes or label is None or label in rounds[number - 1] and rounds[number - 1][label]
            }
            for number, tally in enumerate(rounds)
        ]
        if len({round(sum(tally.values()), 2) for tally in rounds}) != 1:
            raise ValueError(f"{key}: the rounds do not add up to one total, {[sum(t.values()) for t in rounds]}")
        if len(rounds) == 1 and existing is not None:
            raise ValueError(f"{key}: decided in round 1")
        seats = int(elect[1]) if (elect := re.search(r"Number to be elected:?\s*(\d+)", text)) else 1
        threshold = int(sum(rounds[0].values()) // (seats + 1)) + 1
        stated = re.search(r"Threshold:?\s*([\d,]+)", text)
        if stated is not None and int(stated[1].replace(",", "")) != threshold:
            raise ValueError(f"{key}: the threshold is {stated[1]}, not {threshold}")
        official[key] = (seats, rounds)
        if record is None:
            continue

        path = raw.cached(SOURCE, BASE + record).path
        if record.endswith(".csv"):
            data = list(csv.reader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))
        else:
            workbook = fastexcel.read_excel(str(path))
            data = [
                ["" if cell is None else str(cell).strip() for cell in row]
                for row in workbook.load_sheet(workbook.sheet_names[0], header_row=None, dtypes="string")
                .to_polars()
                .iter_rows()
            ]
        start = next(
            index for index, row in enumerate(data) if row and re.match(r"(precinct|w-p)", row[0], re.IGNORECASE)
        )
        labels = [label for label in rounds[0] if label is not None]
        names: dict[str, str | None] = {"CAROL BECKER": "Becker"} if "Becker" in labels else {}
        for row in data[start + 1 :]:
            for choice in row[1:4]:
                choice = choice.strip()
                if choice.casefold() in SKIPPED or choice.casefold() == "overvote" or choice in names:
                    continue
                found = [
                    label
                    for label in labels
                    if label.casefold() == choice.casefold()
                    or choice.upper() == "UWI"
                    and label == "Undeclared Write-ins"
                    or len(choice) == 3
                    and choice.isupper()
                    and re.sub(r"[^A-Z]", "", label.split()[-1].upper())[:3] == choice
                ]
                if len(found) > 1:
                    found = [label for label in found if re.sub(r"[^A-Z]", "", label.upper())[:3] == choice]
                    found = found if len(found) == 1 else [None]
                if not found:
                    found = [
                        label
                        for label in labels
                        if label.casefold().split()[0] == choice.casefold().split()[0]
                        and label.casefold().split()[-1] == choice.casefold().split()[-1]
                    ]
                if not found:
                    found = difflib.get_close_matches(
                        choice.casefold(), [label.casefold() for label in labels], 1, 0.85
                    )
                    found = [label for label in labels if label.casefold() in found]
                if not found:
                    found = [label for label in labels if "write" in label.casefold()] or [None]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} summary rows for the ranking {choice!r}")
                names[choice] = found[0]
        ballots: dict[tuple[str, tuple[str | None, ...]], int] = {}
        for row in data[start + 1 :]:
            if len(row) < 4 or not row[0].strip():
                continue
            ((ward, precinct),) = re.findall(
                r"^(?:MINNEAPOLIS )?W?-?(\d+)[- ]P?-?(\w+)$", row[0].strip(), re.IGNORECASE
            )
            unit = f"W-{int(ward)} P-{precinct.lstrip('0') or '0'}"
            ranking = tuple(
                None
                if choice.strip().casefold() in SKIPPED or choice.strip().casefold() == "overvote"
                else names[choice.strip()]
                for choice in row[1:4]
            )
            count = int(float(row[4])) if len(row) > 4 and row[4].strip() else 1
            ballots[unit, ranking] = ballots.get((unit, ranking), 0) + count
        steps: list[tuple[dict[str, int], set[str]]] = []
        elected: set[str] = set()
        for number in range(1, len(rounds)):
            before, after = rounds[number - 1], rounds[number]
            surplus = {
                label
                for label, votes in before.items()
                if seats > 1
                and label is not None
                and votes >= threshold
                and after.get(label)
                and after[label] < votes - 0.00005
            }
            elected |= surplus
            factors = {
                label: (round(votes * 10**4) - threshold * 10**4) * 10**4 // round(votes * 10**4)
                if label in surplus
                else 10**4
                for label, votes in before.items()
                if label is not None and votes and (label in surplus or not after.get(label))
            }
            steps.append(
                (
                    factors,
                    {label for label, votes in after.items() if label is not None and votes and label not in elected},
                )
            )
        counts: dict[tuple[str, int, str | None], int] = {}
        for (unit, ranking), count in ballots.items():
            first = next(
                (pos for pos, choice in enumerate(ranking) if choice is not None and choice in rounds[0]), None
            )
            if first is None:
                continue
            pieces: dict[int | None, int] = {first: 10**4}
            for number in range(len(rounds)):
                if number:
                    factors, continuing = steps[number - 1]
                    moved: dict[int | None, int] = {}
                    for pos, weight in pieces.items():
                        factor = 0 if pos is None else factors.get(ranking[pos] or "", 0)
                        share = weight if factor == 10**4 else weight * factor // 10**4
                        if share < weight:
                            moved[pos] = moved.get(pos, 0) + weight - share
                        if share:
                            target = (
                                next(
                                    (later for later in range(pos + 1, len(ranking)) if ranking[later] in continuing),
                                    None,
                                )
                                if pos is not None
                                else None
                            )
                            moved[target] = moved.get(target, 0) + share
                    pieces = moved
                for pos, weight in pieces.items():
                    label = None if pos is None else ranking[pos]
                    counts[unit, number + 1, label] = counts.get((unit, number + 1, label), 0) + count * weight
        tally = pl.DataFrame(
            [
                (unit, number, label, counts.get((unit, number, label), 0) / 10**4)
                for unit in sorted({unit for unit, _ in ballots})
                for number, standing in enumerate(rounds, 1)
                for label in {*standing, *(rounds[number - 2] if number > 1 else ())}
            ],
            schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Float64},
            orient="row",
        )
        sums = tally.group_by("round", "label").agg(pl.col("votes").sum())
        for number, expected in enumerate(rounds, 1):
            found = {
                label: round(votes, 4)
                for _, label, votes in sums.filter(pl.col("round") == number).iter_rows()
                if votes
            }
            if found != {label: round(votes, 4) for label, votes in expected.items() if votes}:
                raise ValueError(
                    f"{key} round {number}: cast vote record and summary differ on "
                    f"{ {label: (found.get(label), expected.get(label)) for label in {*found, *expected} if found.get(label, 0) != expected.get(label, 0)} }"
                )
        frames.append(tally.with_columns(key=pl.lit(key)))
    (PARSED / "official.json").write_text(
        json.dumps(
            {
                key: {
                    "seats": seats,
                    "rounds": [[[label, votes] for label, votes in tally.items()] for tally in rounds],
                }
                for key, (seats, rounds) in official.items()
            }
        )
    )
    pl.concat(frames).write_parquet(PARSED / "tallies.parquet")

    rows = []
    with connect() as conn:
        for vintage, prefix in PLANS.items():
            for text, area, geom, point in conn.execute(
                """select n.text, a.id, encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex')
                   from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   join area_parent p on p.area_id = a.id join area_code h on h.area_id = p.parent_id and h.code = '27053'
                   join area_geometry g on g.area_id = a.id
                   where a.scheme_code = 'us.precinct' and c.code like %s and n.text ilike 'Minneapolis W-%%'""",
                [prefix + ":MN:%"],
            ).fetchall():
                ((ward, precinct),) = re.findall(r"^Minneapolis W-\s*(\d+) P-\s*(\w+),", text)
                rows.append((vintage, f"W-{int(ward)} P-{precinct.lstrip('0') or '0'}", str(area), geom, point))
    frame = pl.DataFrame(rows, schema=["plan", "unit", "area", "geom", "point"], orient="row")
    if frame.select("plan", "unit").is_duplicated().any():
        raise ValueError("two areas share a precinct in one plan")
    frame.write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    official: dict[str, tuple[int, list[dict[str | None, float]]]] = {
        key: (contest["seats"], [{label: votes for label, votes in tally} for tally in contest["rounds"]])
        for key, contest in json.loads((PARSED / "official.json").read_text()).items()
    }
    tallies = pl.read_parquet(PARSED / "tallies.parquet")
    shapes = {
        (plan, unit): (geom, point)
        for plan, unit, _, geom, point in pl.read_parquet(PARSED / "precincts.parquet").iter_rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minneapolis's ranked-choice contests for park board, the Board of Estimate and Taxation, and the "
            "2023 and 2025 mayor and city council, 2009 to 2025, with every round by precinct tabulated from the cast "
            "vote records",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        county = cs.area("us.county", "27053")
        city = cs.area("us.place", "2743000")
        whole = cs.unit("us.place", "2743000")
        (polity,) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchone() or (
            None,
        )
        (state,) = conn.execute(
            "select polity_id from polity_territory where area_id = %s", [cs.area("us.state", "27")]
        ).fetchone() or (None,)
        if polity is None or state is None or city in cs.minted or whole in cs.minted:
            raise ValueError("Minneapolis and Minnesota must already exist")
        for result_set, note in conn.execute(
            """select n.entity_id, n.id from note n join result_set r on r.id = n.entity_id
               join changeset h on h.id = r.changeset_id
               where h.pipeline = %s and n.kind = 'methodology' and n.body_md like '%%exhausts at an overvote%%'""",
            [SOURCE],
        ).fetchall():
            cs.add("note", id=note, entity_id=result_set, kind="methodology", body_md=DERIVED)
        irv = cs.key("us.electoral_system", "minneapolis:irv")
        stv = cs.key("us.electoral_system", "minneapolis:stv")
        if irv in cs.minted:
            raise ValueError("minneapolis:irv must already exist")
        cs.add(
            "electoral_system",
            id=stv,
            polity_id=polity,
            rules={
                "formula": "stv",
                "quota": "droop",
                "surplus": "weighted_inclusive_gregory",
                "maxRankings": 3,
                "batchElimination": True,
            },
            rules_version=1,
        )
        name(stv, "Minneapolis single transferable vote")

        pending = [key for key in CONTESTS if key in REDONE or cs.key(f"{SOURCE}.result_set", key) in cs.minted]
        known = {
            vintage for (vintage,) in conn.execute("select vintage from boundary_set where scheme_code = 'us.precinct'")
        }
        precincts: dict[str, dict[str, UUID]] = {}
        for key, unit in sorted(set(tallies.filter(pl.col("key").is_in(pending)).select("key", "unit").iter_rows())):
            day, plan = key[:10], key[:4]
            code = f"{day}:MN:27053:{unit}"
            if unit in precincts.get(day, {}):
                continue
            precincts.setdefault(day, {})[unit] = cs.unit("us.precinct", code)
            area = cs.area("us.precinct", code)
            if area not in cs.minted:
                continue
            cs.add("area", id=area, scheme_code="us.precinct")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=county)
            name(area, f"Minneapolis {unit}, Hennepin County")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=date.fromisoformat(day),
                valid_to=date.fromisoformat(day) + timedelta(days=1),
                geom=shapes[plan, unit][0],
                label_point=shapes[plan, unit][1],
                quality="official",
                source_id=None,
            )
            cs.add(
                "reporting_unit",
                id=precincts[day][unit],
                polity_id=polity,
                scheme_code="us.precinct",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=date.fromisoformat(day),
                valid_to=date.fromisoformat(day) + timedelta(days=1),
            )
        for day in sorted(set(precincts) - known):
            cs.add(
                "boundary_set",
                scheme_code="us.precinct",
                vintage=day,
                valid_from=date.fromisoformat(day),
                valid_to=date.fromisoformat(day) + timedelta(days=1),
                as_of=date.fromisoformat(day),
            )

        people: dict[tuple[str, str], set[tuple[UUID, date, bool]]] = {}
        for text, person, held, area in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id, e.date_from, t.area_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
               where t.area_id = any(%s)""",
            [[county, city]],
        ).fetchall():
            words = spell(text)
            people.setdefault((words[0], words[-1]), set()).add((person, held, area == city))

        done: set[UUID] = set()
        for key in pending:
            existing, summary, record = CONTESTS[key]
            day = date.fromisoformat(key[:10])
            seats, rounds = official[key]
            office = key.split(":", 3)[3]
            kind, _, district_number = office.partition(":")
            election = cs.key("us.election", f"{day}:MN:2743000")
            if election in cs.minted and election not in done:
                done.add(election)
                parent = cs.key("us.election", f"{day}:MN")
                if parent in cs.minted:
                    cs.add(
                        "election",
                        id=parent,
                        polity_id=state,
                        kind="local",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    name(parent, f"{day.year} Minnesota local elections")
                    cs.add("slug", path=f"us/mn/{day}-local", entity_id=parent)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} Minneapolis local election")
                cs.add("slug", path=f"us/mn/minneapolis/{day}-local", entity_id=election)
            if kind in BODIES:
                owner = cs.key("us.body", f"MN:2743000:{kind}")
                if owner in cs.minted and owner not in done:
                    done.add(owner)
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                    name(owner, BODIES[kind])
            else:
                owner = cs.key(
                    "us.office" if kind == "mayor" else "us.body",
                    f"MN:2743000:{'mayor' if kind == 'mayor' else 'council'}",
                )
                if owner in cs.minted:
                    raise ValueError(f"{key}: the {kind} must already exist")
            district = city
            top = whole
            if district_number not in ("", "at-large"):
                code = f"27:2743000:{'council' if kind == 'city-council' else kind}:district-{district_number}"
                district = cs.area("us.local_district", code)
                top = cs.unit("us.local_district", code)
                if kind == "city-council" and district in cs.minted:
                    raise ValueError(f"{key}: {code} must already exist")
                if district in cs.minted and district not in done:
                    done.add(district)
                    cs.add("area", id=district, scheme_code="us.local_district")
                    cs.add("area_code", area_id=district, code=code, valid_from=None)
                    cs.add("area_parent", area_id=district, parent_id=city, valid_from=None)
                    name(district, f"{BODIES[kind]}, District {district_number}")
                    cs.add(
                        "reporting_unit",
                        id=top,
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=code,
                        area_relation="equals",
                        area_id=district,
                        kind="standard",
                    )
            seat = {
                "mayor": "Mayor",
                "city-council": f"City Council, Ward {district_number}",
                "park-board": "Park and Recreation Commissioner "
                + ("At Large" if district_number == "at-large" else f"District {district_number}"),
                "board-of-estimate-and-taxation": "Board of Estimate and Taxation",
            }[kind]
            system = irv if seats == 1 else stv
            rows = tallies.filter(pl.col("key") == key)
            if existing is None:
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office" if kind == "mayor" else "body_seats",
                    office_id=owner if kind == "mayor" else None,
                    body_id=None if kind == "mayor" else owner,
                    district_area_id=district,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                    term="full",
                )
                tail = {
                    "mayor": "mayor",
                    "city-council": f"council-district-{district_number}",
                    "park-board": f"park-board-{'at-large' if district_number == 'at-large' else f'district-{district_number}'}",
                    "board-of-estimate-and-taxation": "board-of-estimate-and-taxation",
                }[kind]
                cs.add("slug", path=f"us/mn/minneapolis/{day}-local/{tail}", entity_id=contest)
                name(
                    contest,
                    f"{day.year} Minneapolis mayoral election"
                    if kind == "mayor"
                    else f"{day.year} {'Minneapolis City Council' if kind == 'city-council' else BODIES[kind]} election"
                    + (f", District {district_number}" if district_number not in ("", "at-large") else "")
                    + (", At Large" if district_number == "at-large" else ""),
                )
                if kind == "park-board" and district_number != "at-large":
                    if rows.is_empty():
                        cs.add("note", entity_id=contest, kind="caveat", body_md=BOUNDARY)
                    else:
                        geom, point = conn.execute(
                            """select encode(st_asewkb(st_multi(st_union(g))), 'hex'),
                                 encode(st_asewkb(st_pointonsurface(st_union(g))), 'hex')
                               from (select st_geomfromewkb(decode(h, 'hex')) g from unnest(%s::text[]) h) x""",
                            [[shapes[key[:4], unit][0] for unit in rows["unit"].unique()]],
                        ).fetchone() or (None, None)
                        cs.add(
                            "area_geometry",
                            area_id=district,
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                            geom=geom,
                            label_point=point,
                            quality="synthetic",
                            source_id=None,
                        )
            else:
                contest = cs.key("us.contest", existing)
                if contest in cs.minted or key not in REDONE:
                    raise ValueError(f"{key}: {existing} must already be published")

            sources = []
            for file, label in ((summary, "tabulation summary statement"), (record, "cast vote record")):
                if file is None:
                    continue
                title = f"{day:%B} {day.day}, {day.year} Minneapolis municipal election: {seat}, {label}"
                source = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=BASE + file,
                    license="Public record",
                )
                cs.artifact(raw.cached(SOURCE, BASE + file), source)
                sources.append(source)

            ballot = BALLOT.get(office, {}) if day.year == 2009 else {}
            current = {
                " ".join(spell(text)): option
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            options: dict[str, UUID] = {}
            scattered = None
            for label in (label for label in rounds[0] if label is not None):
                if label not in ballot and ("write" in label.casefold() or len(spell(label)) == 1 or ballot):
                    if scattered is None:
                        found = [
                            option
                            for (option,) in conn.execute(
                                "select id from contest_option where contest_id = %s and kind = 'write_in'", [contest]
                            ).fetchall()
                        ]
                        if len(found) > 1:
                            raise ValueError(f"{key}: {len(found)} write-in options")
                        scattered = found[0] if found else cs.key("us.option", f"{key}:write_in:scattered")
                        if not found:
                            cs.add(
                                "contest_option",
                                id=scattered,
                                contest_id=contest,
                                kind="write_in",
                                ballot_label="Write-in",
                            )
                    options[label] = scattered
                    continue
                display = ballot.get(label) or (
                    " ".join(reversed(label.split(", "))) if re.fullmatch(r"[^,]+, [^,]+", label) else label
                )
                words = spell(display)
                if existing is not None:
                    if " ".join(words) not in current:
                        raise ValueError(f"{key}: no option for {display}")
                    options[label] = current[" ".join(words)]
                    continue
                known_people = people.get((words[0], words[-1]), set())
                matches = {person for person, _, _ in known_people}
                for preferred in (
                    {person for person, held, _ in known_people if held == day},
                    {person for person, _, local in known_people if local},
                ):
                    if len(preferred) == 1:
                        matches = preferred
                        break
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(display))
                    if person in cs.minted and person not in {p for found in people.values() for p, _, _ in found}:
                        cs.add("person", id=person)
                        name(person, display)
                    people.setdefault((words[0], words[-1]), set()).add((person, day, True))
                options[label] = cs.key("us.option", f"{key}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=label,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len({option for option in options.values() if option != scattered}) != len(
                [option for option in options.values() if option != scattered]
            ):
                raise ValueError(f"{key}: two candidates share an option")

            result = cs.key(f"{SOURCE}.result_set", f"{key}:2" if key in REDONE else key)
            previous = {
                default: cs.key(f"{SOURCE}.result_set", key if default else f"{key}:precincts")
                for default in (True, False)
                if key in REDONE
            }
            for old in previous.values():
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            ("id", "election_id", "contest_id", "stage", "kind", "source_id", "as_of", "completeness"),
                            conn.execute(
                                """select id, election_id, contest_id, stage, kind, source_id, as_of, completeness
                                   from result_set where id = %s""",
                                [old],
                            ).fetchone()
                            or (),
                            strict=True,
                        )
                    ),
                    status="superseded",
                    is_default=False,
                    supersedes_id=None,
                )
            final = rounds[-1]
            winners = sorted((label for label in final if label is not None), key=lambda label: -final[label])[:seats]
            for option in set(options.values()):
                held = [label for label, other in options.items() if other == option]
                for outcome in (
                    ("won", "elected")
                    if set(held) & set(winners)
                    else ("lost" if any(final.get(label) for label in held) else "eliminated",)
                ):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            units = {"top": str(top), **{unit: str(precincts[key[:10]][unit]) for unit in rows["unit"].unique()}}
            if district != city and not rows.is_empty() and key not in REDONE:
                cs.frame(
                    "reporting_unit_area",
                    pl.DataFrame(
                        {
                            "unit_id": [units[unit] for unit in rows["unit"].unique().sort()],
                            "area_id": str(district),
                            "valid_from": day,
                            "valid_to": day + timedelta(days=1),
                        }
                    ),
                )
            sets = [
                (
                    result,
                    True,
                    OFFICIAL + (SURPLUS if seats > 1 else ""),
                    sources[0],
                    0,
                    pl.DataFrame(
                        [
                            ("top", number, label, float(tally.get(label, 0)))
                            for number, tally in enumerate(rounds, 1)
                            for label in rounds[0]
                            if label in tally
                        ],
                        schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Float64},
                        orient="row",
                    ),
                )
            ]
            if record is not None:
                sets.append(
                    (
                        cs.key(f"{SOURCE}.result_set", f"{key}:precincts:2" if key in REDONE else f"{key}:precincts"),
                        False,
                        DERIVED,
                        sources[1],
                        1,
                        rows.select("unit", "round", "label", "votes"),
                    )
                )
            for result_set, default, note, source, flags, values in sets:
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
                    supersedes_id=previous.get(default),
                )
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
                    frame.group_by(
                        unit_id="unit_id",
                        option_id=pl.col("label").replace_strict(
                            {label: str(option) for label, option in options.items()}, default=None
                        ),
                        measure=pl.when(pl.col("label").is_null())
                        .then(21)
                        .otherwise(pl.col("label").replace_strict({"ballots": 3, "valid": 4}, default=1))
                        .cast(pl.Int16),
                        count_round=pl.col("round").cast(pl.Int16),
                    )
                    .agg(value=pl.col("votes").sum().round(4))
                    .select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id="unit_id",
                        option_id="option_id",
                        channel=pl.lit(0, pl.Int16),
                        measure="measure",
                        count_round="count_round",
                        flags=pl.lit(flags, pl.Int16),
                        value="value",
                    ),
                )
        return cs.write()
