import json
import re
import subprocess
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.lascruces_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New Mexico Secretary of State"
CONTESTS = {
    "2025-11-04:council:6": (
        "2025 Las Cruces City Council election, District 6",
        "https://www.sos.nm.gov/wp-content/uploads/2025/11/RcvShortReport-Dist-6.pdf",
    ),
}
UNOFFICIAL = (
    "The Doña Ana County Clerk's RCV short report for the contest, marked unofficial results, run on November "
    "5, 2025, and posted by the New Mexico Secretary of State. Each round eliminated the last-place candidate "
    "and moved those ballots to their next continuing choice. Each candidate's value is a running total; a "
    "candidate eliminated in a round shows 0 in the next. Exhausted counts every ballot not counting for a "
    "candidate in that round: blanks, overvotes, and ballots with no continuing choice. Round 1 is higher than"
    " the first choices in the certified canvass, which stays the default set."
)


def fetch() -> None:
    for _, url in CONTESTS.values():
        response = httpx.get(url, timeout=120, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError(f"{url}: not a PDF")
        raw.store(
            SOURCE, url, response.content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
        )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests: dict[str, dict[str, object]] = {}
    for key, (_, url) in CONTESTS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
        ).stdout
        rows: dict[str, list[str]] = {}
        for line in text.splitlines():
            match = re.fullmatch(r"\s*([A-Za-z].*?)\s{2,}(-?[\d,]+%?(?:\s+-?[\d,.]+%?)*)\s*", line)
            if match and not match.group(1).startswith("Page"):
                rows[match.group(1).strip()] = match.group(2).split()
        names = [label for label, tokens in rows.items() if any(token.endswith("%") for token in tokens)]
        continuing = [int(token.replace(",", "")) for token in rows["Continuing Ballots Total"]]
        stopped = [int(token.replace(",", "")) for token in rows["Non Transferable Total"]]
        rounds: list[dict[str, int]] = [{} for _ in continuing]
        for label in names:
            tokens = rows[label]
            votes = [int(tokens[i - 1].replace(",", "")) for i, token in enumerate(tokens) if token.endswith("%")]
            if len(votes) != len(continuing):
                raise ValueError(f"{key}: {label}")
            for number, value in enumerate(votes):
                rounds[number][label] = value
        total = continuing[0] + stopped[0]
        for number, tally in enumerate(rounds):
            if sum(tally.values()) != continuing[number] or continuing[number] + stopped[number] != total:
                raise ValueError(f"{key} round {number + 1}")
        contests[key] = {"ballots": total, "rounds": [list(tally.items()) for tally in rounds]}
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Las Cruces's mayor and council contests from 2019 restated as IRV, with the unofficial rounds of "
            "the 2025 District 6 race",
            polity="US",
        )

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "3539380")
        if city in cs.minted:
            raise ValueError("Las Cruces must already exist")
        (polity,) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchone() or (
            None,
        )
        irv = cs.key("us.electoral_system", "las-cruces:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        cs.add(
            "name", entity_id=irv, text="Las Cruces ranked-choice voting", lang="en", kind="official", is_preferred=True
        )
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
        for (contest,) in conn.execute(
            """select k.id from contest k join election e on e.id = k.election_id
               join polity_territory t on t.polity_id = e.polity_id
               where t.area_id = %s and e.date_from >= '2019-11-01' and k.kind in ('office', 'body_seats')""",
            [city],
        ).fetchall():
            row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
            cs.add("contest", id=contest, **dict(zip(columns, row or (), strict=True)), electoral_system_id=irv)
        for key, (title, url) in CONTESTS.items():
            found = conn.execute(
                """select k.id, k.election_id from contest k join name n on n.entity_id = k.id
                   join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
                   where n.text = %s and t.area_id = %s""",
                [title, city],
            ).fetchall()
            if len(found) != 1:
                raise ValueError(f"{key}: {len(found)} contests named {title}")
            ((contest, election),) = found
            unit = cs.unit("us.local_district", f"35:3539380:council:district-{key[-1]}")
            if unit in cs.minted:
                raise ValueError(f"{key}: the district unit must already exist")
            day = datetime.fromisoformat(key[:10])
            heading = (
                f"{day:%B} {day.day}, {day.year} Las Cruces municipal election: City Councilor, District {key[-1]}, "
                "RCV short report"
            )
            source = cs.source(PUBLISHER, heading)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=heading,
                url=url,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), source)
            current = {
                tuple(spell(text)[:1] + spell(text)[-1:]): option
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            record = contests[key]
            rounds = [{label: votes for label, votes in tally} for tally in record["rounds"]]
            options: dict[str, UUID] = {}
            for name in rounds[0]:
                words = spell(name)
                if (words[0], words[-1]) not in current:
                    raise ValueError(f"{key}: no option for {name}")
                options[name] = current[words[0], words[-1]]
            result = cs.key(f"{SOURCE}.result_set", key)
            if result not in cs.minted:
                continue
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="preliminary",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=UNOFFICIAL)
            ballots = record["ballots"]
            values = [(None, 3, 0, ballots), (None, 4, 1, sum(rounds[0].values()))]
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, ballots - sum(tally.values())))
                values.extend(
                    (options[name], 1, number, votes)
                    for name, votes in tally.items()
                    if not any(earlier.get(name) == 0 for earlier in rounds[: number - 1])
                )
            for option, measure, number, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=0,
                    value=votes,
                )
            final = rounds[-1]
            winner = max(final, key=lambda name: final[name])
            for name, option in options.items():
                for outcome in ("won", "elected") if name == winner else ("lost" if final[name] else "eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
