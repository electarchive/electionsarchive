import difflib
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ri_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Rhode Island Board of Elections"
BASE = "https://elections.ri.gov/publications/Election_Publications/Countbooks/"
BOOKS = {
    1996: "1996 Count Book",
    1998: "1998 Count Book",
    2000: "2000 Count Book",
    2002: "2002 Count Book",
    2004: "2004 Count Book",
    2006: "2006 Count Book",
    2008: "2008 Count Book",
    2010: "2010 Countbook",
    2012: "2012 Countbook Official",
    2014: "2014 Countbook Final",
    2016: "2016 Count Book",
    2018: "2018 Count Book",
}
HISTORY_YEARS = list(range(1996, 2020, 2))
OLD_PRIMARIES = {
    1998: (date(1998, 9, 15), 622880, 86159),
    2000: (date(2000, 9, 12), 634300, 97689),
    2002: (date(2002, 9, 10), 665012, 149359),
}
OLD_FIXES = {
    1998: {"littlecompton": (2675, 180), "middletown": (9993, 423), "narragansett": (11198, 1743), "scituate": (4133, 336)},
}
ABBREVIATIONS = {"e": "east", "n": "north", "s": "south", "w": "west"}
COUNTIES = {
    "44001": "barrington bristol warren",
    "44003": "coventry eastgreenwich warwick westgreenwich westwarwick",
    "44005": "jamestown littlecompton middletown newport portsmouth tiverton",
    "44007": "burrillville centralfalls cranston cumberland eastprovidence foster glocester johnston lincoln "
    "northprovidence northsmithfield pawtucket providence scituate smithfield woonsocket",
    "44009": "charlestown exeter hopkinton narragansett newshoreham northkingstown richmond southkingstown westerly",
}
TOWN_COUNTY = {town: code for code, towns in COUNTIES.items() for town in towns.split()}
ROW = re.compile(r"^\s*(?:\d\s+)?([A-Za-z][A-Za-z .]+?)\s+([\d,]+)\s+([\d,]+)\s+([\d.]+)\s*%")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
MONTH = re.compile("(" + "|".join(MONTHS) + r")\s+\d{1,2},?\s+\d{4}", re.I)


def url(year: int) -> str:
    return BASE + BOOKS[year].replace(" ", "%20") + ".pdf"


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def number(text: str) -> int:
    return int(text.replace(",", ""))


def fetch() -> None:
    for year in BOOKS:
        try:
            raw.cached(SOURCE, url(year))
            continue
        except LookupError:
            pass
        response = httpx.get(url(year), headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=300)
        response.raise_for_status()
        raw.store(SOURCE, url(year), response.content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def text_of(year: int) -> list[str]:
    result = subprocess.run(["pdftotext", "-layout", str(raw.cached(SOURCE, url(year)).path), "-"], capture_output=True, text=True, check=True)
    return result.stdout.replace("\x0c", "\n").split("\n")


def blocks(lines: list[str]) -> list[dict]:
    found = []
    seen = set()
    i = 0
    while i < len(lines):
        match = ROW.match(lines[i])
        if not (match and plain(match[1]) in TOWN_COUNTY):
            i += 1
            continue
        j = i
        rows: dict[str, tuple[int, int]] = {}
        total = None
        gap = 0
        while j < len(lines) and gap < 8:
            row = ROW.match(lines[j])
            if row:
                name = plain(row[1])
                if name in TOWN_COUNTY:
                    rows.setdefault(name, (number(row[2]), number(row[3])))
                elif "total" in name:
                    total = (number(row[2]), number(row[3]))
                    break
                gap = 0
            else:
                gap += 1
            j += 1
        context = "\n".join(lines[max(0, i - 30) : i])
        months = MONTH.findall(context)
        if len(rows) == len(TOWN_COUNTY):
            eligible = sum(v[0] for v in rows.values())
            cast = sum(v[1] for v in rows.values())
            valid = total is None or (total[0] == eligible and 0 <= total[1] - cast <= total[1] // 8)
            key = (eligible, cast)
            if valid and key not in seen:
                seen.add(key)
                found.append(
                    {
                        "rows": rows,
                        "month": months[-1].lower() if months else None,
                        "preference": "PREFERENCE" in "\n".join(lines[max(0, i - 15) : i]).upper(),
                        "eligible": eligible,
                        "cast": cast,
                    }
                )
        i = j + 1
    return found


def old_primary(year: int) -> dict[str, tuple[int, int]]:
    lines = text_of(year)
    start = next(
        i
        for i, line in enumerate(lines)
        if re.search(r"R(HODE ISLAND|[lI]) STATEWIDE PRIMARY", line)
        and "September" in "\n".join(lines[i : i + 3])
        and "TURNOUT" in "\n".join(lines[i : i + 7]).upper()
    )
    rows = dict(OLD_FIXES.get(year, {}))
    for line in lines[start : start + 80]:
        match = re.match(r"^\W*(?:\d\s+|split\s+)?([A-Za-z][A-Za-z. ]+?)\s+([\d,.]+)\s+([\d,.]+)\s", line)
        if not match:
            continue
        words = match[1].split()
        if len(words[0].rstrip(".")) == 1:
            words[0] = ABBREVIATIONS.get(words[0].rstrip(".").lower(), "")
        name = plain(" ".join(words))
        if name in TOWN_COUNTY:
            rows.setdefault(name, (int(re.sub(r"[.,]", "", match[2])), int(re.sub(r"[.,]", "", match[3]))))
    _, eligible, cast = OLD_PRIMARIES[year]
    got = (sum(v[0] for v in rows.values()), sum(v[1] for v in rows.values()))
    if len(rows) != len(TOWN_COUNTY) or abs(got[0] - eligible) > eligible // 100 or abs(got[1] - cast) > cast // 100:
        raise ValueError(f"{year} primary: {len(rows)} towns {got} vs printed {(eligible, cast)}")
    print(f"{year} primary: {got} vs printed {(eligible, cast)}")
    return rows


def parse() -> None:
    elections: dict[date, dict[str, tuple[int, int]]] = {}
    with connect() as conn:
        days = {
            start: kind
            for start, kind in conn.execute(
                """select e.date_from, e.kind from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':RI(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
            )
        }
    for year in BOOKS:
        in_year = sorted(d for d in days if d.year == year)
        primaries = [d for d in in_year if days[d] == "primary"]
        generals = [d for d in in_year if days[d] == "general"]
        taken = set()
        for block in blocks(text_of(year)):
            day = None
            if block["preference"] and len(primaries) > 1:
                day = primaries[0]
            elif block["month"]:
                matches = [d for d in in_year if MONTHS[d.month - 1] == block["month"] and d not in taken]
                day = matches[0] if matches else None
            elif block["cast"] / block["eligible"] > 0.3:
                day = generals[0] if generals else None
            else:
                day = primaries[-1] if primaries else None
            if day is None or day in taken:
                print(f"{year}: block {block['eligible']}/{block['cast']} unplaced")
                continue
            taken.add(day)
            elections[day] = block["rows"]
    for year, (day, _, _) in OLD_PRIMARIES.items():
        elections[day] = old_primary(year)
    state_rows = []
    for book, years in ((2018, HISTORY_YEARS), (2002, list(range(1986, 2004, 2)))):
        lines = text_of(book)
        start = next(i for i, line in enumerate(lines) if re.match(r"(?i)\s*STATEWIDE ELIGIBILITY & VOTER TURNOUT", line))
        statewide: dict[str, list[int]] = {}
        municipal: dict[str, dict[str, list[int]]] = {}
        town = None
        for line in lines[start:]:
            stripped = re.sub(r"^\d+\s+(?=[A-Za-z])", "", line.strip())
            values = re.findall(r"[\d,]{3,}", stripped)
            kind = re.match(r"(?i)(eligible|turnout)\b", stripped)
            if kind and len(values) == len(years):
                target = statewide if town is None else municipal.setdefault(town, {})
                target[kind[1].lower()] = [number(v) for v in values]
            elif re.fullmatch(r"MUNICIPAL ELIGIBILITY & VOTER TURNOUT", stripped):
                town = ""
            elif re.fullmatch(r"[A-Za-z][A-Za-z .]+", stripped):
                close = difflib.get_close_matches(plain(stripped), list(TOWN_COUNTY), n=1, cutoff=0.85)
                if close:
                    town = close[0]
                    municipal.setdefault(town, {})
        if len(municipal) != len(TOWN_COUNTY) or any(len(v) != 2 for v in municipal.values()):
            raise ValueError(f"{book} history table has {len(municipal)} towns")
        for index, year in enumerate(years):
            if book == 2002 and year >= 1996:
                continue
            day = next((d for d, kind in days.items() if d.year == year and kind == "general"), None)
            if day is None:
                continue
            eligible = sum(municipal[t]["eligible"][index] for t in municipal)
            cast = sum(municipal[t]["turnout"][index] for t in municipal)
            state = (statewide["eligible"][index], statewide["turnout"][index])
            state_rows.append({"date": day, "registered": state[0], "voters": state[1]})
            ok = eligible == state[0] and abs(cast - state[1]) <= max(60, state[1] // 200)
            print(f"history {year}: towns {eligible}/{cast} state {state} {'ok' if ok else 'mismatch'}")
            if ok and day not in elections:
                elections[day] = {t: (municipal[t]["eligible"][index], municipal[t]["turnout"][index]) for t in municipal}
    lines = text_of(2000)
    start = next(i for i, line in enumerate(lines) if re.match(r"(?i)\s*STATEWIDE ELIGIBILITY & VOTER TURNOUT", line))
    history = {}
    for line in lines[start : start + 12]:
        match = re.match(r"\s*(ELIGIBLE|TURNOUT)\s+((?:[\d,]{6,}\s+){9}[\d,]{6,})\s*$", line)
        if match:
            history[match[1]] = [number(v) for v in match[2].split()]
    for index, year in enumerate((1982, 1984)):
        day = next((d for d, kind in days.items() if d.year == year and kind == "general"), None)
        if day is not None:
            state_rows.append({"date": day, "registered": history["ELIGIBLE"][index], "voters": history["TURNOUT"][index]})
    rows = []
    for day, towns in elections.items():
        for code in COUNTIES:
            members = [v for t, v in towns.items() if TOWN_COUNTY[t] == code]
            rows.append({"date": day, "code": code, "registered": sum(m[0] for m in members), "voters": sum(m[1] for m in members)})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "counties.parquet")
    pl.DataFrame(state_rows).write_parquet(PARSED / "state.parquet")
    print(frame.group_by("date").agg(pl.col("registered").sum(), pl.col("voters").sum()).sort("date"))


def emit() -> Path:
    counties = pl.read_parquet(PARSED / "counties.parquet")
    state = pl.read_parquet(PARSED / "state.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's registered voters and ballots cast by county for its primaries and generals of 1982–2018 (primaries from 1998), "
            "summed from the Board of Elections' municipal turnout tables in the count books, and statewide where the municipal tables do not add up",
            polity="US",
        )
        elections = {
            start: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':RI(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Count Books"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://elections.ri.gov/elections/publications",
                license="Public record",
            )
        for year in BOOKS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        published = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and source_id = %s and status <> 'withdrawn'", (source,)
            )
        }
        covered = set()
        for (day,), rows in counties.group_by("date", maintain_order=True):
            if day not in elections:
                print(f"{day}: no election")
                continue
            covered.add(day)
            value_key, election_id = elections[day]
            if election_id in published:
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=round(rows.height / 5, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        for day, registered, voters in state.select("date", "registered", "voters").iter_rows():
            if day in covered or day not in elections:
                continue
            value_key, election_id = elections[day]
            if election_id in published:
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:state")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=1,
            )
            for measure, value in ((2, registered), (3, voters)):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", "44"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
