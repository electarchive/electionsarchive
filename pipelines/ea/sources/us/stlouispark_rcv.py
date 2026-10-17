import html
import itertools
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.stlouispark_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of St. Louis Park City Clerk"
SITE = "https://www.stlouisparkmn.gov/government/elections/"
PAGES = {
    "2019-11-05": ("20260613055609", SITE + "election-results"),
    "2021-11-02": ("20260413080057", SITE + "ranked-choice-voting/2021-election-results"),
    "2023-11-07": ("20250406101658", SITE + "ranked-choice-voting/2023-election-results"),
    "2025-11-04": ("20260513220600", SITE + "ranked-choice-voting/2025-election-results"),
}
METHOD = "Data is transcribed from the city clerk's round-by-round results page, as archived by the Internet Archive. Votes cast for the office exclude blank and partially defective ballots, which are counted here as invalid. The city counts overvotes and undervotes as continuing in round 1 and drops them in round 2; here they are shown as exhausted from round 1. Undeclared write-ins are grouped into one write-in line."
MERGED = "This record combines the 2019 at-large A and at-large B races into one contest. The city clerk's results, which list each seat as a separate contest, are the default."


def fetch() -> None:
    for stamp, url in PAGES.values():
        raw.fetch(SOURCE, f"https://web.archive.org/web/{stamp}id_/{url}", suffix=".html")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rounds: dict[str, list[dict[str, int]]] = {}
    totals: dict[str, tuple[str, str, int, int]] = {}
    for day, (stamp, url) in PAGES.items():
        text = raw.cached(SOURCE, f"https://web.archive.org/web/{stamp}id_/{url}").path.read_text(errors="replace")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
        if f"held Nov. {int(day[8:])}, {day[:4]}" not in text:
            raise ValueError(f"{day}: page is for another election")
        for title, block in re.findall(
            r"(Mayor|Council member (?:at large [AB]|[Ww]ard \d)) Candidate Number of votes Percent of votes (.*?)"
            r"Sec\. 10-4\.",
            text,
        ):
            seat = "Mayor" if title == "Mayor" else title.removeprefix("Council member ")
            seat = re.sub(r"(?i)^ward (\d)", r"Ward \1", seat).replace("at large", "At-Large")
            figures = {
                label.casefold(): int(value.replace(",", ""))
                for label, value in re.findall(
                    r"(Total votes continuing|Total votes cast for this office|Totally blank|Partially defective"
                    r"|Exhausted ballots|Total ballots cast)(?: ballots| citywide| in Ward \d)? ([\d,]+)(?= |$)",
                    block,
                )
            }
            rows = block[: block.index("Total votes continuing")]
            if "Exhausted ballots" in rows:
                rows = rows[: rows.index("Exhausted ballots")]
            tally: dict[str, int] = {}
            exhausted = 0
            for label, votes in re.findall(r"(\S.*?) (?:([\d,]+) [\d.]+%|(?i:eliminated)(?: [\d.]+%)?)(?= |$)", rows):
                label = label.strip()
                count = int(votes.replace(",", "")) if votes else 0
                if label in ("Overvote", "Undervote"):
                    exhausted += count
                elif label.startswith("Undeclared write-in"):
                    tally["Write-in"] = count
                else:
                    tally[label] = count
            tally.setdefault("Write-in", 0)
            valid = figures["total votes cast for this office"]
            if sum(tally.values()) + exhausted != figures["total votes continuing"]:
                raise ValueError(f"{day} {seat}: rows do not sum to votes continuing")
            if figures["total votes continuing"] + figures.get("exhausted ballots", 0) != valid:
                raise ValueError(f"{day} {seat}: continuing and exhausted do not sum to votes cast")
            ballots = figures["total ballots cast"]
            invalid = figures["totally blank"] + figures["partially defective"]
            if valid + invalid != ballots:
                raise ValueError(f"{day} {seat}: votes cast, blank, and defective do not sum to ballots")
            threshold = re.search(r"Threshold ?= ?([\d,]+)", block)
            if threshold is not None and int(threshold[1].replace(",", "")) != valid // 2 + 1:
                raise ValueError(f"{day} {seat}: threshold")
            key = f"{day}:{seat}"
            if key in rounds:
                if set(tally) - set(rounds[key][-1]) or totals[key][2:] != (ballots, invalid):
                    raise ValueError(f"{key}: later round does not follow")
                rounds[key].append({label: tally.get(label, 0) for label in rounds[key][-1]})
            else:
                rounds[key] = [tally]
                totals[key] = (url, stamp, ballots, invalid)
    for key, tallies in rounds.items():
        if 2 * max(tallies[-1].values()) <= totals[key][2] - totals[key][3]:
            raise ValueError(f"{key}: no winner above the threshold")
        for earlier, later in itertools.pairwise(tallies):
            if any(later[label] < votes for label, votes in earlier.items() if later[label]):
                raise ValueError(f"{key}: a continuing candidate lost votes")
    if len(rounds) != 14 or sum(len(tallies) > 1 for tallies in rounds.values()) != 3:
        raise ValueError(f"expected 14 contests, three of them past round 1, not {len(rounds)}")
    contests = {
        key: {"url": url, "stamp": stamp, "ballots": ballots, "invalid": invalid, "rounds": rounds[key]}
        for key, (url, stamp, ballots, invalid) in totals.items()
    }
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="St. Louis Park, Minnesota's ranked-choice mayor and council elections, 2019 to 2025, from the city "
            "clerk's round-by-round results pages, with the 2019 and 2021 contests restated as IRV and the 2019 "
            "at-large seats split into A and B",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr", "write-in")]

        city = cs.area("us.place", "2757220")
        council = cs.key("us.body", "MN:2757220:council")
        mayor = cs.key("us.office", "MN:2757220:mayor")
        if {city, council, mayor} & cs.minted:
            raise ValueError("St. Louis Park, its council, and its mayor must already exist")
        ((polity,),) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchall()
        irv = cs.key("us.electoral_system", "st-louis-park:irv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=polity,
            rules={"formula": "irv", "maxRankings": 3},
            rules_version=1,
        )
        name(irv, "St. Louis Park ranked-choice voting")

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
        demoted: set[UUID] = set()
        for (contest,) in conn.execute(
            """select k.id from contest k join election e on e.id = k.election_id
               where e.polity_id = %s and e.date_from in ('2019-11-05', '2021-11-02')
               and k.kind in ('office', 'body_seats')""",
            [polity],
        ).fetchall():
            (row,) = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchall()
            cs.add("contest", id=contest, **dict(zip(columns, row, strict=True)), electoral_system_id=irv)

        people: dict[tuple[str, str], set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id where e.polity_id = %s""",
            [polity],
        ).fetchall():
            words = spell(text)
            people.setdefault((words[0], words[-1]), set()).add(person)

        done: set[object] = set()
        for key, record in contests.items():
            day = date.fromisoformat(key[:10])
            seat = key[11:]
            existing = conn.execute(
                "select id from election where polity_id = %s and date_from = %s and parent_id is not null",
                [polity, day],
            ).fetchall()
            if existing:
                ((election,),) = existing
            else:
                election = cs.key("us.election", f"{day}:MN:2757220")
                if election not in done:
                    done.add(election)
                    ((parent,),) = conn.execute(
                        """select e.id from election e join name n on n.entity_id = e.id
                           where e.date_from = %s and e.parent_id is null and n.text = %s""",
                        [day, f"{day.year} Minnesota local elections"],
                    ).fetchall()
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
                    name(election, f"{day.year} St. Louis Park local election")
                    cs.add("slug", path=f"us/mn/st-louis-park/{day}-local", entity_id=election)
            area, top = city, cs.unit("us.place", "2757220")
            if seat.startswith("Ward"):
                code = f"27:2757220:council:district-{seat[-1]}"
                area, top = cs.area("us.local_district", code), cs.unit("us.local_district", code)
                if {area, top} & cs.minted:
                    raise ValueError(f"{code} must already exist")
            heading = (
                f"{day.year} St. Louis Park mayoral election"
                if seat == "Mayor"
                else f"{day.year} St. Louis Park City Council election, {seat.replace('Ward', 'District')}"
            )
            found = conn.execute(
                "select k.id from contest k join name n on n.entity_id = k.id where n.text = %s and k.election_id = %s",
                [heading, election],
            ).fetchall()
            if seat.startswith("At-Large") and day.year == 2019:
                ((merged,),) = conn.execute(
                    "select k.id from contest k join name n on n.entity_id = k.id where n.text = %s",
                    [f"{day.year} St. Louis Park City Council election, At-Large"],
                ).fetchall()
                if merged not in demoted:
                    demoted.add(merged)
                    for values in conn.execute(
                        """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                             supersedes_id from result_set where contest_id = %s and is_default""",
                        [merged],
                    ).fetchall():
                        fields = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of")
                        fields += ("completeness", "supersedes_id")
                        cs.add("result_set", **dict(zip(fields, values, strict=True)), is_default=False)
                        cs.add("note", entity_id=values[0], kind="caveat", body_md=MERGED)
            if found:
                ((contest,),) = found
                for values in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id from result_set where contest_id = %s and is_default
                         and changeset_id not in (select id from changeset where pipeline = %s)""",
                    [contest, SOURCE],
                ).fetchall():
                    fields = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of")
                    fields += ("completeness", "supersedes_id")
                    cs.add("result_set", **dict(zip(fields, values, strict=True)), is_default=False)
            else:
                part = "mayor" if seat == "Mayor" else "council-" + slug(seat.replace("Ward", "District"))
                contest = cs.key("us.contest", f"{day}:MN:2757220:{part}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office" if seat == "Mayor" else "body_seats",
                    office_id=mayor if seat == "Mayor" else None,
                    body_id=None if seat == "Mayor" else council,
                    district_area_id=area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=irv,
                    default_result_kind="counted",
                    term="full",
                )
                name(contest, heading)
                cs.add("slug", path=f"us/mn/st-louis-park/{day}-local/{part}", entity_id=contest)
            current = {
                tuple(spell(text)[:1] + spell(text)[-1:]): option
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            rounds = record["rounds"]
            options: dict[str, UUID] = {}
            for label in rounds[0]:
                text = label.replace(" (write-in)", "")
                if label == "Write-in":
                    options[label] = cs.key("us.option", f"{contest}:write_in:scattered")
                    cs.add("contest_option", id=options[label], contest_id=contest, kind="write_in", ballot_label=label)
                    continue
                words = spell(text)
                if (words[0], words[-1]) in current:
                    options[label] = current[words[0], words[-1]]
                    continue
                matches = people.get((words[0], words[-1]), set())
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(f"{words[0]} {words[-1]}"))
                    if person not in done:
                        done.add(person)
                        cs.add("person", id=person)
                        name(person, text)
                    people[words[0], words[-1]] = {person}
                options[label] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=text)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=text,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len(set(options.values())) != len(options):
                raise ValueError(f"{key}: two candidates share an option")
            url = f"https://web.archive.org/web/{record['stamp']}id_/{record['url']}"
            source = cs.source(PUBLISHER, f"{day.year} ranked-choice election results")
            if source not in done:
                done.add(source)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=f"{day.year} ranked-choice election results",
                    url=record["url"],
                    license="Public record",
                )
                cs.artifact(raw.cached(SOURCE, url), source)
            result = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
            valid = record["ballots"] - record["invalid"]
            values = [(None, 3, 0, record["ballots"]), (None, 5, 0, record["invalid"]), (None, 4, 1, valid)]
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, valid - sum(tally.values())))
                values.extend(
                    (options[label], 1, number, votes)
                    for label, votes in tally.items()
                    if number == 1 or rounds[number - 2][label]
                )
            for option, measure, number, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=top,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=0,
                    value=votes,
                )
            final = rounds[-1]
            winner = max(final, key=lambda label: final[label])
            for label, option in options.items():
                for outcome in ("won", "elected") if label == winner else ("lost" if final[label] else "eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
