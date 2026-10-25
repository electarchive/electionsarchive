import csv
import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "us.maricopa"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FOLDERS = [Path(__file__).parents[4] / "data/inbox/manual" / mf for mf in ("MF-081", "MF-082")]
URL = "https://elections.maricopa.gov/results-and-data/historic-results.html?year={year}#{name}"


def files() -> list[tuple[date, Path]]:
    found = []
    for folder in FOLDERS:
        for path in sorted(folder.glob("*.txt")):
            stamp = re.match(r"(\d+)-(\d+)-(\d{4})", path.name)
            if stamp is None or "Summary" in path.name or "Unofficial" in path.name or "Export" in path.name:
                continue
            found.append((date(int(stamp[3]), int(stamp[1]), int(stamp[2])), path))
    return found


def fetch() -> None:
    for day, path in files():
        raw.store(
            SOURCE,
            URL.format(year=day.year, name=path.name),
            path.read_bytes(),
            suffix=".txt",
            retrieved_at=datetime(2026, 10, 25, tzinfo=UTC),
            media_type="text/plain",
        )


def parse() -> None:
    csv.field_size_limit(10**9)
    PARSED.mkdir(parents=True, exist_ok=True)
    total = 0
    for day, path in files():
        rows = []
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            head = next(reader)
            if len(head) < 5:
                continue
            index = {name: i for i, name in enumerate(head)}
            if "CONTEST_FULL_NAME" in index:
                contest, precinct, candidate = (
                    index["CONTEST_FULL_NAME"],
                    index["PRECINCT_NAME"],
                    index["CANDIDATE_FULL_NAME"],
                )
                votes, kind, write_in, vote_for = (
                    index["TOTAL"],
                    index["CONTEST_TYPE"],
                    index["IS_WRITEIN"],
                    index["contest_vote_for"],
                )
                for line in reader:
                    if len(line) > votes and line[kind] != "-2":
                        rows.append(
                            (
                                day,
                                line[contest],
                                int(line[vote_for] or 0),
                                line[precinct],
                                line[candidate],
                                None,
                                int(line[votes] or 0),
                                line[write_in] == "1",
                            )
                        )
            else:
                contest, precinct, candidate = index["ContestName"], index["PrecinctName"], index["CandidateName"]
                votes, kind, party, vote_for = (
                    index["Votes"],
                    index["ContestType"],
                    index["CandidateAffiliation"],
                    index["ContestVoteFor"],
                )
                for line in reader:
                    if len(line) > votes and line[kind] in ("Candidacy", "Measure") and line[votes]:
                        rows.append(
                            (
                                day,
                                line[contest],
                                int(line[vote_for] or 0),
                                line[precinct],
                                line[candidate],
                                line[party] or None,
                                int(line[votes]),
                                line[candidate].lower().startswith("write"),
                            )
                        )
        frame = pl.DataFrame(
            rows,
            schema=["day", "contest", "vote_for", "precinct", "candidate", "party", "votes", "write_in"],
            orient="row",
        ).unique(subset=["day", "contest", "precinct", "candidate"], keep="first", maintain_order=True)
        frame.write_parquet(PARSED / f"{day}-{path.stem[:40].replace(' ', '_')}.parquet")
        total += frame.height
        print(path.name, frame.height, flush=True)
    print(total, "rows")
