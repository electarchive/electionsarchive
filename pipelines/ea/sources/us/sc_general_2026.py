import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.mp_cec_live import pick, words
from ea.store import DATA, connect

SOURCE = "us.sc_general_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
PAGE = "https://www.enr-scvotes.org/SC/127300/reports/detailxml.zip"
INBOX = DATA / "inbox" / "manual" / "MF-613" / "detailxml.zip"
PUBLISHER = "South Carolina State Election Commission"
TITLE = "2026 Statewide General Election results, certified by the State Board of Canvassers"
CHANNELS = {"Election Day": 1, "Early Voting": 2, "Absentee By Mail": 3}
STATEWIDE = {
    "governor and lieutenant governor": "governor",
    "secretary of state": "secretary-of-state",
    "state treasurer": "treasurer",
    "attorney general": "attorney-general",
    "comptroller general": "comptroller-general",
    "state superintendent of education": "superintendent-of-education",
    "commissioner of agriculture": "commissioner-of-agriculture",
    "u s senate": "senate",
    "state senate district 15 unexpired term": "senate:15:gs",
}
DISTRICT = re.compile(r"^(u s|state) house of representatives district (\d+)$")
NOTE = (
    "County totals by voting method from the State Election Commission's results system, the version posted "
    "on November 10, 2026, the day the State Board of Canvassers met. Early voting, absentee by mail, and "
    "election day are separate channels; provisional and failsafe ballots are pooled on the provisional channel."
)


def contest_key(text: str) -> str | None:
    plain = " ".join(re.findall(r"[a-z]+|\d+", text.lower()))
    if plain in STATEWIDE:
        return f"{DAY}:SC:{STATEWIDE[plain]}"
    house = DISTRICT.match(plain)
    if house is None:
        return None
    return f"{DAY}:SC:house:{house[2]}" + (":g" if house[1] == "state" else "")


def fetch() -> None:
    raw.store(
        SOURCE,
        PAGE,
        INBOX.read_bytes(),
        suffix=".zip",
        retrieved_at=datetime.fromtimestamp(INBOX.stat().st_mtime, UTC),
        media_type="application/zip",
    )


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, PAGE).path) as archive:
        root = ElementTree.fromstring(archive.read("detail.xml"))
    stamp = datetime.strptime((root.findtext("Timestamp") or "").replace(" EST", ""), "%m/%d/%Y %I:%M:%S %p")
    as_of = stamp.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC).isoformat()
    rows = []
    for contest in root.iter("Contest"):
        key = contest_key(contest.get("text") or "")
        if key is None:
            continue
        for choice in contest.findall("Choice"):
            for vote_type in choice.findall("VoteType"):
                channel = CHANNELS.get(vote_type.get("name") or "", 5)
                for county in vote_type.findall("County"):
                    rows.append(
                        {
                            "contest": key,
                            "choice": choice.get("text"),
                            "party": choice.get("party"),
                            "total": int(choice.get("totalVotes") or 0),
                            "county": county.get("name"),
                            "channel": channel,
                            "votes": int(county.get("votes") or 0),
                            "as_of": as_of,
                        }
                    )
    frame = (
        pl.DataFrame(rows)
        .group_by("contest", "choice", "party", "total", "county", "channel", "as_of")
        .agg(pl.col("votes").sum())
    )
    mismatched = (
        frame.group_by("contest", "choice", "total")
        .agg(pl.col("votes").sum())
        .filter(pl.col("votes") != pl.col("total"))
    )
    if mismatched.height:
        raise ValueError(f"county votes do not sum to the choice totals: {mismatched}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    print(frame["contest"].n_unique(), "contests", frame["votes"].sum(), "votes")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "results.parquet")
    as_of = datetime.fromisoformat(frame["as_of"][0])
    with connect() as conn:
        counties = {}
        for text, unit in conn.execute(
            """select n.text, u.id from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '45%'"""
        ).fetchall():
            counties.setdefault(" ".join(words(text.removesuffix(" County"))), unit)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina November 3, 2026 general election: statewide offices, U.S. Senate, U.S. House, and state legislative contests by county and voting method, as certified",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.enr-scvotes.org/SC/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, PAGE), source)
        for (key,), rows in frame.group_by("contest", maintain_order=True):
            found = conn.execute(
                """select c.id, c.election_id from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchall()
            if len(found) != 1:
                raise ValueError(f"{key}: {len(found)} contests")
            contest, election = found[0]
            options = conn.execute(
                """select o.id, o.kind, case when o.kind = 'candidate'
                   then coalesce(nullif(ca.ballot_name, ''), o.ballot_label) else o.ballot_label end
                   from contest_option o
                   left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            labels = {option: words(label or "") for option, kind, label in options if kind != "write_in"}
            write_in = next((option for option, kind, _ in options if kind == "write_in"), None)
            chosen = {}
            for choice, party in rows.select("choice", "party").unique(maintain_order=True).iter_rows():
                if choice == "Write-In":
                    if write_in is None:
                        write_in = cs.key("us.option", f"{key}:write_in:scattered")
                        cs.add(
                            "contest_option",
                            id=write_in,
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Scattered",
                        )
                    chosen[choice] = write_in
                    continue
                option = pick(choice.removeprefix(f"{party} ") if party else choice, labels)
                if option is None or option in chosen.values():
                    raise ValueError(f"{key}: no single option for {choice}")
                chosen[choice] = option
            missing = set(labels) - set(chosen.values())
            if missing:
                raise ValueError(f"{key}: {len(missing)} options have no votes in the file")
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                as_of=as_of,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            totals = dict(rows.group_by("choice").agg(pl.col("votes").sum()).iter_rows())
            ranked = sorted((c for c in totals if c != "Write-In"), key=lambda c: -totals[c])
            if len(ranked) > 1 and totals[ranked[0]] == totals[ranked[1]]:
                raise ValueError(f"{key}: tie for first")
            cs.add("outcome", result_set_id=result_set, option_id=chosen[ranked[0]], kind="elected", seats=1)
            for choice, county, channel, votes in rows.select("choice", "county", "channel", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[" ".join(words(county))],
                    option_id=chosen[choice],
                    channel=channel,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
