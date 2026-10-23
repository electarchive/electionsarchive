import io
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.mi_proposals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-020"
URL = "https://mvic.sos.state.mi.us/VoteHistory/GetElectionResultFile?electionId={}"
FILES = {
    "2012STATE_GENERAL_MI_CENR_BY_COUNTY.txt": 652,
    "2014STATE_PRIMARY_MI_CENR_BY_COUNTY.txt": 661,
    "2014STATE_GENERAL_MI_CENR_BY_COUNTY.txt": 662,
    "2015MAY_CONSOLIDATED_MI_CENR_BY_COUNTY.txt": 664,
    "2018STATE_GENERAL_MI_CENR_BY_COUNTY.txt": 676,
    "2020STATE_GENERAL_MI_CENR_BY_COUNTY.txt": 683,
}
PUBLISHER = "Michigan Department of State"
TITLE = "Michigan Voter Information Center: official election results by county"
TOKENS = {
    ("2014-08-05", "14-1"): "michigan-use-tax-and-community-stabilization-share-proposal-1",
    ("2014-11-04", "14-1"): "michigan-wolf-hunting-referendum-proposal-1",
    ("2014-11-04", "14-2"): "michigan-natural-resources-commission-referendum-proposal-2",
    ("2015-05-05", "15-1"): "michigan-sales-tax-increase-for-transportation-amendment-proposal-1",
}
METHOD = (
    "County totals are the Michigan Department of State's official canvass of this proposal, as published by the "
    "Michigan Voter Information Center. They tie exactly to the statewide totals held for the question."
)
DRIFT = (
    "County totals are the Michigan Department of State's official canvass of this proposal, as published by the "
    "Michigan Voter Information Center. The statewide totals held for the question, from the English Wikipedia, are "
    "within 0.1 percent below these county sums, which are the canvassed figures."
)


def fetch() -> None:
    for name, election in FILES.items():
        raw.store(
            SOURCE,
            URL.format(election),
            (INBOX / name).read_bytes(),
            suffix=".txt",
            retrieved_at=datetime(2026, 10, 23, tzinfo=UTC),
            media_type="text/plain",
        )


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st ")): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '26%'"""
            ).fetchall()
        }
    frames = []
    for election in FILES.values():
        text = raw.cached(SOURCE, URL.format(election)).path.read_bytes().decode("latin-1")
        frame = pl.read_csv(
            io.StringIO(text.split("\n", 1)[1].replace("\r", "")),
            separator="\t",
            infer_schema=False,
            quote_char=None,
        )
        frames.append(
            frame.filter(pl.col("OfficeCode(text)") == "90").select(
                day=pl.col("ElectionDate").str.to_date(),
                proposal=pl.col("OfficeDescription").str.extract(r"(\d\d-\d)"),
                title=pl.col("OfficeDescription").str.replace(r"^State Proposal - ", ""),
                code=pl.col("CountyName")
                .str.to_lowercase()
                .str.replace(r"^st\.? ", "st ")
                .str.replace(r"^gd\. ", "grand ")
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                answer=pl.col("CandidateLastName").str.to_uppercase(),
                votes=pl.col("CandidateVotes").cast(pl.Int64),
            )
        )
    frame = pl.concat(frames)
    if frame["code"].null_count():
        raise ValueError("unmatched counties")
    if set(frame["answer"]) != {"YES", "NO"}:
        raise ValueError(f"unexpected answers: {set(frame['answer'])}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("day", "proposal").agg(pl.col("code").n_unique().alias("counties")).sort("day", "proposal"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan's statewide ballot proposals from 2012 to 2020 by county, from the state's official canvass",
            polity="US-MI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://mvic.sos.state.mi.us/votehistory/",
            license="Public record",
        )
        for election in FILES.values():
            cs.artifact(raw.cached(SOURCE, URL.format(election)), source)
        wiki: dict[str, tuple[UUID, UUID, UUID, UUID, int, int]] = {
            key: (contest, election, yes_option, no_option, yes, no)
            for key, contest, election, yes_option, no_option, yes, no in conn.execute(
                """select x.value, c.id, c.election_id,
                          max(o.id::text) filter (where o.ballot_label = 'Yes')::uuid,
                          max(o.id::text) filter (where o.ballot_label = 'No')::uuid,
                          sum(v.value) filter (where o.ballot_label = 'Yes')::bigint,
                          sum(v.value) filter (where o.ballot_label = 'No')::bigint
                   from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                     and x.value ~ '^20[0-9-]+:MI:question:wikipedia:'
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k
                   join contest_option o on o.contest_id = c.id and o.k = v.option_k
                   group by 1, 2, 3"""
            ).fetchall()
        }
        for (day, proposal), rows in frame.group_by("day", "proposal", maintain_order=True):
            token = TOKENS.get((str(day), str(proposal)), f"michigan-proposal-{str(proposal)[-1]}-")
            found = [key for key in wiki if key.startswith(f"{day}:") and f":wikipedia:{token}" in key]
            if len(found) != 1:
                raise ValueError(f"{day} {proposal}: {len(found)} statewide contests")
            key = found[0]
            contest, election, yes_option, no_option, official_yes, official_no = wiki[key]
            yes = int(rows.filter(pl.col("answer") == "YES")["votes"].sum())
            no = int(rows.filter(pl.col("answer") == "NO")["votes"].sum())
            print(f"{key[:70]}: county {yes}/{no} held {official_yes}/{official_no}")
            if abs(yes - official_yes) > official_yes * 0.001 or abs(no - official_no) > official_no * 0.001:
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
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
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD if (yes, no) == (official_yes, official_no) else DRIFT,
            )
            answers = {"YES": yes_option, "NO": no_option}
            for code, answer, votes in rows.select("code", "answer", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.county", code),
                    option_id=answers[answer],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
