import html
import re
import time
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.tx_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://elections.sos.state.tx.us/"
HOUSE = re.compile(r"^U\. ?S\. Representative District (\d+)$")
ELECTIONS = {7: "democratic", 8: "republican", 53: "democratic", 55: "republican", 62: "democratic", 63: "republican"}
PUBLISHER = "Texas Secretary of State"
TITLE = "Texas election history: House primaries by county, 1994–1998"


def races(election: int) -> list[tuple[int, int]]:
    text = raw.cached(SOURCE, f"{SITE}elchist{election}_raceselect.htm").path.read_text(errors="replace")
    return [
        (int(found[1]), int(district[1]))
        for found in re.finditer(r'<OPTION value="?(\d+)"?[^>]*>([^<]*)', text, re.IGNORECASE)
        if (district := HOUSE.match(re.sub(r"\s+", " ", found[2]).strip()))
    ]


def fetch() -> None:
    for election in ELECTIONS:
        raw.fetch(SOURCE, f"{SITE}elchist{election}_raceselect.htm", suffix=".htm")
        time.sleep(1)
        for race, _ in races(election):
            raw.fetch(SOURCE, f"{SITE}elchist{election}_race{race}.htm", suffix=".htm")
            time.sleep(1)


def cells(row: str, tag: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
        for cell in re.findall(rf"<{tag}[^>]*>(.*?)(?=<{tag}[\s>]|</TR>|$)", row, re.IGNORECASE | re.DOTALL)
    ]


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '48%%'"""
            ).fetchall()
        }
        rows: list[dict[str, object]] = []
        for election, chamber in ELECTIONS.items():
            for race, district in races(election):
                text = raw.cached(SOURCE, f"{SITE}elchist{election}_race{race}.htm").path.read_text(errors="replace")
                stamp = re.search(r"<H3[^>]*>(\d+)/(\d+)/(\d{4})</H3>", text, re.IGNORECASE)
                if stamp is None:
                    raise ValueError(f"{election} {race}: no date")
                day = date(int(stamp[3]), int(stamp[1]), int(stamp[2]))
                lines = re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.IGNORECASE | re.DOTALL)
                headers = [cells(line, "TH") for line in lines if re.search(r"<TH", line, re.IGNORECASE)]
                data = [cells(line, "TD") for line in lines if re.search(r"<TD", line, re.IGNORECASE)]
                width = headers[-1].index("Votes")
                counties = [row for row in data if row[0] != "ALL COUNTIES"]
                total = next((row for row in data if row[0] == "ALL COUNTIES"), counties[0] if len(counties) == 1 else None)
                if total is None:
                    raise ValueError(f"{election} {race}: no total")
                key = f"{day}:TX:house:{district}:{chamber}"
                known = {
                    int(fec): label
                    for label, fec in conn.execute(
                        """select o.ballot_label, sum(v.value)::bigint from external_id e
                           join contest_option o on o.contest_id = e.entity_id
                           join result_set rs on rs.contest_id = e.entity_id and rs.is_default
                           join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                           where e.scheme = 'us.contest' and e.value = %s and o.kind = 'candidate'
                           group by 1""",
                        [key],
                    ).fetchall()
                }
                names: dict[int, str] = {}
                for column in range(1, width):
                    name = " ".join(
                        row[column] for row in headers[:-1] if column < len(row) and row[column] not in ("...", "Total")
                    ).strip()
                    printed = int(total[column].replace(",", ""))
                    if not name:
                        if printed not in known:
                            break
                        name = known[printed]
                    names[column] = name
                else:
                    for column, name in names.items():
                        votes = [int(row[column].replace(",", "") or 0) for row in counties]
                        if sum(votes) != int(total[column].replace(",", "")):
                            raise ValueError(f"{election} {race} {name}: counties sum to {sum(votes)}, not {total[column]}")
                        rows.extend(
                            {
                                "year": day.year,
                                "chamber": f"house:{chamber}",
                                "day": day,
                                "special": False,
                                "state": "TX",
                                "district": district,
                                "code": fips[re.sub(r"[^a-z]", "", row[0].lower())],
                                "candidate": name.upper(),
                                "party": headers[-1][column],
                                "measure": 1,
                                "writein": False,
                                "votes": value,
                                "kind": "county",
                                "source": "TX",
                            }
                            for row, value in zip(counties, votes, strict=True)
                        )
                    continue
                print(f"{key}: a column without a name matches no FEC total, skipped")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema_overrides={"year": pl.Int32, "district": pl.Int16, "measure": pl.Int16})
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year", "chamber").agg(pl.col("district").n_unique()).sort("year", "chamber"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas's House primaries of 1994, 1996, and 1998 by county from the Secretary of State's election history",
            polity="US-TX",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license="Public record",
        )
        for election in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, f"{SITE}elchist{election}_raceselect.htm"), source)
            for race, _ in races(election):
                cs.artifact(raw.cached(SOURCE, f"{SITE}elchist{election}_race{race}.htm"), source)
        medsl.attach(cs, conn, frame, {"TX": source}, strict=True)
        return cs.write()
