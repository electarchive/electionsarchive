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

SOURCE = "us.santafe_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SOS = "https://www.sos.nm.gov/wp-content/uploads/2025/11/"
CONTESTS = {
    "2018-03-06:mayor": ("2018 Santa Fe mayoral election", "https://santafenm.gov/document_center/document/8052"),
    "2025-11-04:mayor": ("2025 Santa Fe mayoral election", SOS + "RcvShortReport-Mayor.pdf"),
    "2025-11-04:council:1": ("2025 Santa Fe City Council election, District 1", SOS + "RcvShortReport-CC1-1.pdf"),
    "2025-11-04:council:2": ("2025 Santa Fe City Council election, District 2", SOS + "RcvShortReport-CC2.pdf"),
}
DETAILED = (
    "The City of Santa Fe's RCV detailed report for this contest. Data comes from the Dominion tabulation run "
    "on election night, March 6, 2018, and was published with the city's official results. In each round, the "
    "last-place candidate is eliminated and those ballots move to the next continuing choice. Candidate values"
    " are running totals; a candidate eliminated in a round shows 0 in the next. Exhausted counts every ballot"
    " not counting for a candidate in that round: blanks, overvotes, and ballots with no continuing choice."
)
SHORT = (
    "The Santa Fe County Clerk's RCV short report for this contest. Data comes from the Dominion tabulation "
    "run on election night, November 4, 2025, and was posted by the New Mexico Secretary of State as official "
    "results. In each round, the last-place candidate is eliminated and those ballots move to the next "
    "continuing choice. Candidate values are running totals; a candidate eliminated in a round shows 0 in the "
    "next. Exhausted counts every ballot not counting for a candidate in that round: blanks, overvotes, and "
    "ballots with no continuing choice."
)
DISTRICTS = (
    "The 2018 tabulation broken down by city council district, as printed in the report. The four districts "
    "add up to the citywide count in every round."
)
CANVASS = (
    "Round 1 differs slightly from the first choices in the certified canvass (which are kept as a separate "
    "set) because this report was run on election night before the canvass."
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
        regions: dict[str, list[dict[str, int]]] = {}
        other: dict[str, list[int]] = {}
        if "RCV Detailed Report" in text:
            region = None
            for line in text.splitlines():
                stripped = line.strip()
                if re.fullmatch(r"City of Santa Fe|District \d|Cumulative", stripped):
                    region = stripped
                    continue
                if stripped.startswith("Round ") and region is not None:
                    regions.setdefault(region, []).append({})
                    other.setdefault(region, [])
                    continue
                match = re.fullmatch(r"(\S.*?)\s{2,}([\d,]+)\s+([\d.]+%)", stripped)
                if match and region in regions:
                    regions[region][-1][match.group(1)] = int(match.group(2).replace(",", ""))
                match = re.fullmatch(r"Continuing Ballots Total\s+([\d,]+)|Non Transferable Total\s+([\d,]+)", stripped)
                if match and region in regions:
                    other[region].append(int((match.group(1) or match.group(2)).replace(",", "")))
            regions.pop("Cumulative", None)
            for region, rounds in regions.items():
                pairs = other[region]
                if len(pairs) != 2 * len(rounds):
                    raise ValueError(f"{key} {region}: {pairs}")
                for number, tally in enumerate(rounds):
                    continuing, rest = pairs[2 * number], pairs[2 * number + 1]
                    if sum(tally.values()) != continuing or continuing + rest != pairs[0] + pairs[1]:
                        raise ValueError(f"{key} {region} round {number + 1}: {tally}")
            ballots = {region: other[region][0] + other[region][1] for region in regions}
            city = regions.pop("City of Santa Fe")
            for number, tally in enumerate(city):
                for label, votes in tally.items():
                    if sum(rounds[number][label] for rounds in regions.values()) != votes:
                        raise ValueError(f"{key}: the districts do not add up for {label} in round {number + 1}")
            if sum(ballots[region] for region in regions) != ballots["City of Santa Fe"]:
                raise ValueError(f"{key}: the districts' ballots do not add up")
            contests[key] = {
                "ballots": ballots["City of Santa Fe"],
                "rounds": [list(tally.items()) for tally in city],
                "districts": {
                    region.split()[-1]: {"ballots": ballots[region], "rounds": [list(t.items()) for t in rounds]}
                    for region, rounds in regions.items()
                },
            }
            continue
        pages = text.split("\f")
        labeled: list[tuple[str, list[str]]] = []
        extra: list[list[str]] = []
        for page in pages:
            lines = page.splitlines()
            if any(line.strip().startswith("Candidate") for line in lines):
                start = next(i for i, line in enumerate(lines) if line.strip().startswith("Candidate"))
                for line in lines[start + 1 :]:
                    match = re.fullmatch(
                        r"\s*([A-Za-z*].*?)\s{2,}(-?[\d,]+(?:\.\d+)?%?(?:\s+-?[\d,]+(?:\.\d+)?%?)*)\s*", line
                    )
                    if match and not match.group(1).startswith(("Page", "(")):
                        labeled.append((match.group(1).strip(), match.group(2).split()))
            elif labeled:
                for line in lines:
                    if re.fullmatch(r"\s*-?[\d,]+(?:\.\d+)?%?(?:\s+-?[\d,]+(?:\.\d+)?%?)*\s*", line) and line.strip():
                        extra.append(line.split())
        if extra:
            if len(extra) != len(labeled):
                raise ValueError(f"{key}: {len(labeled)} labeled rows and {len(extra)} continued rows")
            labeled = [(label, tokens + more) for (label, tokens), more in zip(labeled, extra, strict=True)]
        rows = {label: tokens for label, tokens in labeled}
        names = [label for label, tokens in labeled if any(token.endswith("%") for token in tokens)]
        count = len(rows["Continuing Ballots Total"])
        rounds = [{} for _ in range(count)]
        for label in names:
            tokens = rows[label]
            votes = [int(tokens[i - 1].replace(",", "")) for i, token in enumerate(tokens) if token.endswith("%")]
            if len(votes) != count:
                raise ValueError(f"{key}: {label} has {len(votes)} rounds")
            for number, value in enumerate(votes):
                rounds[number][label] = value
        continuing = [int(token.replace(",", "")) for token in rows["Continuing Ballots Total"]]
        stopped = [int(token.replace(",", "")) for token in rows["Non Transferable Total"]]
        for label in ("Blanks", "Exhausted", "Overvotes"):
            values = [int(token.replace(",", "")) for token in rows[label][0::2]]
            if len(values) != count:
                raise ValueError(f"{key}: {label}")
        total = continuing[0] + stopped[0]
        for number, tally in enumerate(rounds):
            if sum(tally.values()) != continuing[number] or continuing[number] + stopped[number] != total:
                raise ValueError(f"{key} round {number + 1}: {sum(tally.values())} against {continuing[number]}")
        contests[key] = {"ballots": total, "rounds": [list(tally.items()) for tally in rounds]}
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Santa Fe's ranked-choice contests with official round reports: the 2018 and 2025 mayors and two "
            "2025 council districts, with every Santa Fe mayor and council contest from 2018 restated as IRV",
            polity="US",
        )

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "3570500")
        whole = cs.unit("us.place", "3570500")
        if {city, whole} & cs.minted:
            raise ValueError("Santa Fe must already exist")
        (polity,) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchone() or (
            None,
        )
        irv = cs.key("us.electoral_system", "santa-fe:irv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=polity,
            rules={"formula": "irv"},
            rules_version=1,
        )
        cs.add(
            "name", entity_id=irv, text="Santa Fe ranked-choice voting", lang="en", kind="official", is_preferred=True
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
               where t.area_id = %s and e.date_from >= '2018-03-01' and k.kind in ('office', 'body_seats')""",
            [city],
        ).fetchall():
            row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
            cs.add("contest", id=contest, **dict(zip(columns, row or (), strict=True)), electoral_system_id=irv)
        for key, (title, url) in CONTESTS.items():
            found = conn.execute(
                """select k.id, k.election_id, k.district_area_id from contest k join name n on n.entity_id = k.id
                   join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
                   where n.text = %s and t.area_id = %s""",
                [title, city],
            ).fetchall()
            if len(found) != 1:
                raise ValueError(f"{key}: {len(found)} contests named {title}")
            ((contest, election, district),) = found
            record = contests[key]
            rounds = [{label: votes for label, votes in tally} for tally in record["rounds"]]
            top = whole if district == city else cs.unit("us.local_district", f"35:3570500:council:district-{key[-1]}")
            if top in cs.minted:
                raise ValueError(f"{key}: its reporting unit must already exist")
            for values in conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                     supersedes_id from result_set where contest_id = %s and is_default
                     and changeset_id not in (select id from changeset where pipeline = %s)""",
                [contest, SOURCE],
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
            publisher = "City of Santa Fe" if key.startswith("2018") else "New Mexico Secretary of State"
            day = datetime.fromisoformat(key[:10])
            seat = "Mayor" if key.endswith("mayor") else f"City Councilor, District {key[-1]}"
            label = "RCV detailed report" if key.startswith("2018") else "RCV short report"
            heading = f"{day:%B} {day.day}, {day.year} Santa Fe municipal election: {seat}, {label}"
            source = cs.source(publisher, heading)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
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
            options: dict[str, UUID] = {}
            for name in rounds[0]:
                words = spell(name)
                if (words[0], words[-1]) not in current:
                    raise ValueError(f"{key}: no option for {name}")
                options[name] = current[words[0], words[-1]]
            if len(set(options.values())) != len(options):
                raise ValueError(f"{key}: two candidates share an option")
            sets = [
                (
                    cs.key(f"{SOURCE}.result_set", key),
                    True,
                    [("methodology", DETAILED if key.startswith("2018") else SHORT)]
                    + ([] if key.startswith("2018") else [("caveat", CANVASS)]),
                    [(top, record["ballots"], rounds)],
                )
            ]
            if "districts" in record:
                sets.append(
                    (
                        cs.key(f"{SOURCE}.result_set", f"{key}:districts"),
                        False,
                        [("methodology", DISTRICTS)],
                        [
                            (
                                cs.unit("us.local_district", f"35:3570500:council:district-{number}"),
                                part["ballots"],
                                [{name: votes for name, votes in tally} for tally in part["rounds"]],
                            )
                            for number, part in record["districts"].items()
                        ],
                    )
                )
            for result, default, notes, units in sets:
                if result not in cs.minted:
                    continue
                cs.add(
                    "result_set",
                    id=result,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                for kind, body in notes:
                    cs.add("note", entity_id=result, kind=kind, body_md=body)
                for unit, ballots, tallies in units:
                    if unit in cs.minted:
                        raise ValueError(f"{key}: {unit} must already exist")
                    values = [(None, 3, 0, ballots), (None, 4, 1, sum(tallies[0].values()))]
                    for number, tally in enumerate(tallies, 1):
                        values.append((None, 21, number, ballots - sum(tally.values())))
                        values.extend(
                            (options[name], 1, number, votes)
                            for name, votes in tally.items()
                            if not any(earlier.get(name) == 0 for earlier in tallies[: number - 1])
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
                if default:
                    final = rounds[-1]
                    winner = max(final, key=lambda name: final[name])
                    for name, option in options.items():
                        for outcome in (
                            ("won", "elected") if name == winner else ("lost" if final[name] else "eliminated",)
                        ):
                            cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
