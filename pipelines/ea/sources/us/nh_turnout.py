import html
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nh_turnout"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New Hampshire Secretary of State"
REGISTRATION = (
    "https://web.archive.org/web/20260709183931if_/https://www.sos.nh.gov/party-registration-history-1970-2026"
)
BALLOTS = {2008: "https://web.archive.org/web/20090730165712if_/http://sos.nh.gov/general2008/sumballots08.htm"}
WAYBACK = "https://web.archive.org/web/{}if_/https://sos.nh.gov/media/{}"
PRIMARIES = {
    date(2008, 1, 8): {"ballots": ("20201018154924", "v4fhr4lk/ballotscastpp2008.xls")},
    date(2008, 9, 9): {"ballots": ("20201030135734", "vybpzqj3/ballotscast2008primary.xls")},
    date(2010, 9, 14): {"ballots": ("20201028082234", "u0watiqw/ballotscast2010primary.xls")},
    date(2014, 9, 9): {
        "ballots": ("20201029085009", "ka3pb0wu/ballots-cast-primary-2014.xls"),
        "registered": ("20201023202035", "mqtl1gdx/names-on-checklist-primary-2014.xls"),
    },
    date(2016, 2, 9): {"registered": ("20201018034527", "itxj2z1e/names-on-checklist.xls")},
    date(2016, 9, 13): {"ballots": ("20201018211801", "5mahkqho/ballots-cast-primary-2016-excel.xls")},
    date(2018, 9, 11): {"registered": ("20201018044948", "npqlgdeb/names-on-checklist-2018-primary.pdf")},
    date(2020, 2, 11): {
        "ballots": ("20201017002740", "bvcnbvaz/ballots-cast-2020-presidential-primary-1.xlsx"),
        "registered": ("20201017003420", "vhqfxt3r/names-on-checklist-presidential-primary-2020-1.xlsx"),
    },
    date(2020, 9, 8): {
        "ballots": ("20211113235846", "luobjuyx/ballotscastsp20.xls"),
        "registered": ("20211114003145", "2plocmoz/names-on-checklist-2020-primary.xlsx"),
    },
}
COUNTIES = {
    name: f"33{2 * i + 1:03d}"
    for i, name in enumerate(
        [
            "belknap",
            "carroll",
            "cheshire",
            "coos",
            "grafton",
            "hillsborough",
            "merrimack",
            "rockingham",
            "strafford",
            "sullivan",
        ]
    )
}
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    )
}
URL = "https://www.sos.nh.gov/party-registration-history-1970-2026"
TITLE = "Party Registration History 1970-2026, and Summary Ballots Cast, State General Election, November 4, 2008"
PRIMARY_TITLE = "State and Presidential Primary Elections, Ballots Cast and Names on Checklist by County"


def fetch() -> None:
    raw.fetch(SOURCE, REGISTRATION, suffix=".html")
    for url in BALLOTS.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for files in PRIMARIES.values():
        for stamp, name in files.values():
            raw.fetch(SOURCE, WAYBACK.format(stamp, name), suffix=Path(name).suffix)


def cells(markup: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
        for cell in re.findall(r"<t[dh].*?</t[dh]>", markup, re.DOTALL)
    ]


def county_rows(stamp: str, name: str) -> dict[str, list[int]]:
    path = raw.cached(SOURCE, WAYBACK.format(stamp, name)).path
    if path.suffix == ".pdf":
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        lines = [re.split(r"\s{2,}", line.strip()) for line in text.split("\f")[0].splitlines()]
    else:
        lines = [
            [str(c) for c in r if c is not None and str(c).strip()]
            for r in pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
        ]
    found: dict[str, list[int]] = {}
    for line in lines:
        if not line:
            continue
        label = re.sub(r"\s+county$", "", line[0].strip().lower())
        numbers = [int(float(c.replace(",", ""))) for c in line[1:] if re.fullmatch(r"[\d,]+(\.0)?", c.strip())]
        if (label in COUNTIES or label == "totals") and label not in found:
            found[label] = numbers
    assert len(found) == 11, (name, sorted(found))
    return found


def parse() -> None:
    text = raw.cached(SOURCE, REGISTRATION).path.read_text(errors="ignore")
    snapshots: dict[date, int] = {}
    for row in re.findall(r"<tr.*?</tr>", text, re.DOTALL):
        found = [c for c in cells(row) if c]
        match = re.match(r"([A-Z][a-z]+) (\d+),\s*(\d{4})", found[0]) if found else None
        if match is None or match[1] not in MONTHS:
            continue
        total = re.match(r"[\d,]+", found[-1])
        if total is None:
            continue
        snapshots[date(int(match[3]), MONTHS[match[1]], int(match[2]))] = int(total[0].replace(",", ""))
    rows = []
    for year in range(1970, 2026, 2):
        first = date(year, 11, 1)
        day = first + timedelta(days=(7 - first.weekday()) % 7 + 1)
        near = [(abs((d - day).days), d) for d in snapshots if -3 <= (d - day).days <= 7]
        if near:
            rows.append(
                {
                    "date": day,
                    "kind": "general",
                    "code": "33",
                    "registered": snapshots[min(near)[1]],
                    "voters": None,
                    "as_of": min(near)[1],
                }
            )
    for year, url in BALLOTS.items():
        text = raw.cached(SOURCE, url).path.read_text(errors="ignore")
        totals = [c for c in cells(text) if c]
        index = next(i for i, c in enumerate(totals) if c == "Totals")
        voters = int(totals[index + 3].replace(",", ""))
        for row in rows:
            if row["date"].year == year:
                row["voters"] = voters
    with connect() as conn:
        days = [
            d
            for (d,) in conn.execute(
                """select distinct e.date_from from election e join polity p on p.id = e.polity_id
               where p.iso_code = 'US-NH' and e.kind = 'primary' and e.date_from < current_date order by 1"""
            )
        ]
    for day in days:
        files = PRIMARIES.get(day, {})
        county: dict[str, dict[str, int]] = {code: {} for code in COUNTIES.values()}
        for measure, (stamp, name) in files.items():
            found = county_rows(stamp, name)
            for label, numbers in found.items():
                if measure == "ballots":
                    assert numbers[0] + numbers[1] == numbers[2] and numbers[3] + numbers[4] == numbers[5], (
                        name,
                        label,
                        numbers,
                    )
                    value = numbers[2] + numbers[5]
                else:
                    value = next(numbers[i] for i in range(2, len(numbers)) if sum(numbers[:i]) == numbers[i])
                if label != "totals":
                    county[COUNTIES[label]][measure] = value
            total = sum(c[measure] for c in county.values())
            printed = found["totals"]
            assert total == (
                printed[2] + printed[5]
                if measure == "ballots"
                else next(printed[i] for i in range(2, len(printed)) if sum(printed[:i]) == printed[i])
            ), (name, total, printed)
        if files:
            for code, values in county.items():
                rows.append(
                    {
                        "date": day,
                        "kind": "primary",
                        "code": code,
                        "registered": values.get("registered"),
                        "voters": values.get("ballots"),
                        "as_of": None,
                    }
                )
        if "registered" not in files:
            near = [(abs((d - day).days), d) for d in snapshots if -3 <= (d - day).days <= 7]
            if near:
                rows.append(
                    {
                        "date": day,
                        "kind": "primary",
                        "code": "33",
                        "registered": snapshots[min(near)[1]],
                        "voters": None,
                        "as_of": min(near)[1],
                    }
                )
    frame = pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "kind": pl.String,
            "code": pl.String,
            "registered": pl.Int64,
            "voters": pl.Int64,
            "as_of": pl.Date,
        },
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's statewide registered voters at each November general election 1970-2024 and each primary from 2000 (checklist total in the Secretary of State's "
            "party registration history, the snapshot dated within three days before to seven days after election day), ballots cast in 2008, and the Secretary of State's "
            "county ballots cast (Republican plus Democratic) and names on checklist for the 2008-2020 primaries, from Wayback copies",
            polity="US",
        )
        elections = {
            (value, kind): entity_id
            for value, entity_id, kind in conn.execute(
                """select x.value, x.entity_id, e.kind::text from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':NH(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        sources = {}
        for kind, title in (("general", TITLE), ("primary", PRIMARY_TITLE)):
            sources[kind] = cs.source(PUBLISHER, title)
            if sources[kind] in cs.minted:
                cs.add(
                    "source",
                    id=sources[kind],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=URL,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, REGISTRATION), sources[kind])
        for url in BALLOTS.values():
            cs.artifact(raw.cached(SOURCE, url), sources["general"])
        for files in PRIMARIES.values():
            for stamp, name in files.values():
                cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, name)), sources["primary"])
        for (day, kind), rows in frame.group_by("date", "kind", maintain_order=True):
            found = [(v, i) for (v, k), i in elections.items() if k == kind and v.startswith(day.isoformat())]
            if len(found) != 1:
                print(f"{day} {kind}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
            result_set = cs.key(f"{SOURCE}.result_set", value_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[kind],
                is_default=election_id not in held,
                completeness=1,
            )
            for code, registered, voters, as_of in rows.select("code", "registered", "voters", "as_of").iter_rows():
                if as_of is not None and as_of != day:
                    offset = (as_of - day).days
                    cs.add(
                        "note",
                        id=uuid5(result_set, "registered as of"),
                        entity_id=result_set,
                        kind="methodology",
                        body_md=f"Registered voters is the checklist total as of {as_of.isoformat()}, {abs(offset)} day{'s' if abs(offset) != 1 else ''} "
                        f"{'after' if offset > 0 else 'before'} the election, in the Secretary of State's party registration history; "
                        "it has no figure dated election day. New Hampshire's checklists are not purged in the week after an election.",
                    )
                for measure, value in ((2, registered), (14 if kind == "general" else 3, voters)):
                    if value is None:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.state" if code == "33" else "us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=2 if measure == 2 and as_of is not None and as_of != day else 0,
                        value=value,
                    )
        return cs.write()
