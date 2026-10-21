import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wikipedia_guam_governors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "Guam general election articles, governor results"
NOTE = (
    "Territory-wide votes for governor as tabulated in the English Wikipedia's article on each Guam election, "
    "which cites the Guam Election Commission's reports."
)
PARTIES = {
    "Republican": "republican-party",
    "Democratic": "democratic-party",
}
CONTESTS = [
    (1974, date(1974, 11, 5), "general", "1974 Guamanian gubernatorial election", "box", "1974 Guamanian gubernatorial general election"),
    (1974, date(1974, 11, 14), "runoff", "1974 Guamanian gubernatorial election", "box", "1974 Guamanian gubernatorial runoff election"),
    (1978, date(1978, 11, 7), "general", "1978 Guam general election", "table", "===Governor==="),
    (1990, date(1990, 11, 6), "general", "1990 Guam general election", "box", "1990 Guamanian gubernatorial election"),
    (1998, date(1998, 11, 3), "general", "1998 Guam general election", "results", "===General election==="),
    (2006, date(2006, 11, 7), "general", "2006 Guam general election", "box", "=== Gubernatorial ==="),
    (2010, date(2010, 11, 2), "general", "2010 Guam general election", "infobox", "{{Infobox election"),
    (2014, date(2014, 11, 4), "general", "2014 Guam general election", "box", "2014 Guamanian gubernatorial general election"),
    (2018, date(2018, 11, 6), "general", "2018 Guam general election", "box", "General election results for governor of Guam"),
    (2022, date(2022, 11, 8), "general", "2022 Guam general election", "box", "{{Election box begin no change|title=Election results"),
]


def page(titles: list[str]) -> str:
    return API + urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content|ids",
            "rvslots": "main",
            "titles": "|".join(titles),
            "format": "json",
            "formatversion": 2,
        }
    )


def titles() -> list[str]:
    return sorted({article for _, _, _, article, _, _ in CONTESTS})


def fetch() -> None:
    raw.fetch(SOURCE, page(titles()), suffix=".json")


def fields_of(template: str) -> tuple[str, dict[str, str]]:
    parts, level, current = [], 0, ""
    body = template[2:-2]
    for index, character in enumerate(body):
        pair = body[index : index + 2]
        if pair in ("{{", "[["):
            level += 1
        elif pair in ("}}", "]]") and level:
            level -= 1
        if character == "|" and level == 0:
            parts.append(current)
            current = ""
        else:
            current += character
    parts.append(current)
    return parts[0].strip().lower(), {
        key.strip().lower(): value.strip() for key, _, value in (part.partition("=") for part in parts[1:])
    }


def templates_of(text: str) -> list[str]:
    found, depth, start = [], 0, 0
    for match in re.finditer(r"\{\{|\}\}", text):
        if match[0] == "{{":
            if depth == 0:
                start = match.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                found.append(text[start : match.end()])
    return found


def plain(text: str) -> str:
    text = re.sub(r"\[\[([^|\]]*)(?:\|[^\]]*)?\]\]", r"\1", text)
    text = re.sub(r"\{\{[^{}]*\}\}|'{2,}|<ref[^>]*>.*?</ref>|<ref[^>]*/>", "", text)
    text = re.sub(r"\((?:incumbent|inc\.|write-in)\)", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,;")


def count(text: str) -> int:
    digits = re.sub(r"[^\d]", "", re.sub(r"\{\{[^{}]*\}\}|'{2,}", "", text))
    if not digits:
        raise ValueError(f"no votes in {text!r}")
    return int(digits)


def parse() -> None:
    texts = {
        found["title"]: re.sub(
            r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", found["revisions"][0]["slots"]["main"]["content"], flags=re.DOTALL
        )
        for found in json.loads(raw.cached(SOURCE, page(titles())).path.read_text())["query"]["pages"]
    }
    rows: list[dict[str, object]] = []
    for year, day, stage, article, kind, anchor in CONTESTS:
        text = texts[article]
        start = text.index(anchor)
        entries: list[tuple[str, str, str, str]] = []
        if kind == "box":
            section = text[start : text.index("{{Election box end}}", start)]
            for template in templates_of(section):
                name, fields = fields_of(template)
                if "candidate" in name:
                    entries.append((fields["candidate"], fields.get("party", ""), "", fields["votes"]))
                elif "write-in" in name:
                    entries.append(("Write-ins", "", "", fields["votes"]))
        elif kind == "results":
            section = text[start:]
            _, fields = fields_of(templates_of(section)[0])
            for index in range(1, 9):
                if f"cand{index}" in fields:
                    entries.append(
                        (
                            fields[f"cand{index}"],
                            fields.get(f"party{index}", ""),
                            fields.get(f"vp{index}", ""),
                            fields[f"votes{index}"],
                        )
                    )
                elif f"row{index}" in fields:
                    entries.append(("Write-ins", "", "", fields[f"votes{index}"]))
        elif kind == "table":
            section = text[start : text.index("===Legislature===", start)]
            for line in section.splitlines():
                cells = [cell for cell in line.split("||")]
                if len(cells) == 5 and cells[0].startswith("|align=left|"):
                    entries.append((cells[1].split("|", 1)[1], cells[0].split("|", 2)[2], cells[2].split("|", 1)[1], cells[3]))
        else:
            _, fields = fields_of(templates_of(text[start:])[0])
            for index in (1, 2):
                entries.append(
                    (
                        fields[f"nominee{index}"],
                        fields[f"party{index}"],
                        fields[f"running_mate{index}"],
                        fields[f"popular_vote{index}"],
                    )
                )
        for position, (candidate, party, mate, votes) in enumerate(entries):
            if "<br" in candidate:
                candidate, mate = re.split(r"<br\s*/?>", candidate, maxsplit=1)
            elif "/" in candidate:
                candidate, mate = candidate.split("/", 1)
            elif " and " in candidate:
                candidate, mate = candidate.split(" and ", 1)
            rows.append(
                {
                    "year": year,
                    "day": day,
                    "stage": stage,
                    "position": position,
                    "candidate": plain(candidate),
                    "running_mate": plain(mate),
                    "party": plain(party),
                    "votes": count(votes),
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Guam governor general elections and the 1974 runoff from the English Wikipedia's election articles",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/2018_Guam_general_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, page(titles())), source)
        two_round = cs.key("us.electoral_system", "two_round")
        unit, area = conn.execute(
            """select u.id, u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id where p.iso_code = 'US-GU' and u.scheme_code = 'us.state'"""
        ).fetchone()
        polity = cs.polity("US-GU")
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        held = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value like '%%:GU:governor'"
            ).fetchall()
        }
        office = cs.key("us.office", "governor:GU")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=polity, kind="governor")
            name(office, "Governor of Guam")
        persons: dict[str, UUID] = {}
        generals: dict[int, UUID] = {}
        for (year, day, stage), rows in frame.group_by(["year", "day", "stage"], maintain_order=True):
            runoff = stage == "runoff"
            contest_key = f"{day}:GU:governor"
            if contest_key in held:
                continue
            election_key = f"{day}:GU"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{year} Guam {stage} election")
                cs.add("slug", path=f"us/gu/{day}-{stage}", entity_id=election)
            contest = cs.key("us.contest", contest_key)
            contested_runoff = year == 1974 and not runoff
            if not runoff:
                generals[year] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=generals[year] if runoff else None,
                electoral_system_id=two_round,
                default_result_kind="counted",
            )
            name(contest, f"{year} Guam governor {'runoff' if runoff else 'election'}")
            cs.add("slug", path=f"us/gu/{day}-{stage}/governor", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=f"Territory-wide votes from the English Wikipedia's article on the {year} Guam election, citing the Guam Election Commission.",
            )
            ranked: list[tuple[int, UUID]] = []
            for candidate, mate, party, votes in (
                rows.sort("position").select("candidate", "running_mate", "party", "votes").iter_rows()
            ):
                if candidate.startswith("Write-in"):
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    token = slug(f"{candidate} {mate}")
                    party_key = next((value for label, value in PARTIES.items() if party.startswith(label)), None)
                    if party_key is None and party:
                        party_key = f"GU:{slug(party)}"
                    party_id = None
                    if party_key is not None:
                        party_id = cs.key("us.party", party_key)
                        if party_key not in parties_known:
                            parties_known.add(party_key)
                            cs.add("party", id=party_id, kind="party", polity_id=polity)
                            name(party_id, party)
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=f"{candidate} and {mate}" if mate else candidate,
                    )
                    for role, label in (("head", candidate), ("running_mate", mate)):
                        if not label:
                            continue
                        key = slug(label)
                        if key not in persons:
                            persons[key] = cs.key(f"{SOURCE}.person", f"GU:{key}")
                            if persons[key] in cs.minted:
                                cs.add("person", id=persons[key])
                                name(persons[key], label)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{token}:{role}"),
                            person_id=persons[key],
                            option_id=option,
                            role=role,
                            ballot_name=label if role == "head" else None,
                            is_independent=party_id is None,
                        )
                    ranked.append((votes, option))
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            ranked.sort(key=lambda pair: -pair[0])
            if not contested_runoff:
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="elected", seats=None)
        return cs.write()
