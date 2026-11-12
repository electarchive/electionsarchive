import re
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.names import display
from ea.sources.gb.parish import slug
from ea.store import DATA, connect

SOURCE = "gb.cc_elections"
VERSION = "1"
SCHEME = "gb.community_council"
PARSED = DATA / "parsed" / SOURCE
SOURCES = Path(__file__).parent / "cc_elections_sources.tsv"
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
COUNCILS = {
    "S12000017": (
        "The Highland Council",
        "Highland Council Scheme of Establishment for Community Councils",
        "https://www.highland.gov.uk/downloads/file/3749/community-council-scheme-of-establishment-2025",
        (
            "Community councils in Highland are formed under the Highland Council's Scheme of Establishment for "
            "Community Councils, made under Part IV of the Local Government (Scotland) Act 1973. The council's "
            "returning officer runs the elections: where nominations do not exceed the places, the nominees are "
            "declared elected without a poll; where they exceed them, a postal ballot is held in which each elector "
            "may vote for as many candidates as there are places, and the candidates with the most votes are elected. "
            "A community council is not formed when nominations fall short of the scheme's minimum."
        ),
    ),
}
ALIASES = {
    ("S12000017", "Bettyhill, Strathnaver and Altnahara"): "Bettyhill, Strathnaver and Altnaharra",
    ("S12000017", "Spean Bridge, Roy Bridge and Achnacarry CC"): "Spean Bridge, Roy Bridge and Achnacarry",
    ("S12000017", "Fort Augustus and Glenmorriston"): "Fort Augustus and Glenmoriston",
    ("S12000017", "Dunvegan"): "Dunvegan and District",
    ("S12000017", "Stromeferry and Achmore"): "Stromeferry",
}
SHORT = "Highland Council's notice lists {elected} members elected unopposed to {places} places, leaving {left} vacant."


def fetch() -> None:
    for url in pl.read_csv(SOURCES, separator="\t")["url"]:
        if _cached(url):
            continue
        if "web.archive.org" in url:
            time.sleep(5)
        response = httpx.get(url, headers={"User-Agent": BROWSER}, timeout=90, follow_redirects=True)
        if response.status_code != 200 or not response.content.startswith(b"%PDF"):
            print(f"not a PDF: {url}", file=sys.stderr)
            continue
        raw.store(
            SOURCE,
            url,
            response.content,
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def _cached(url: str) -> bool:
    try:
        raw.cached(SOURCE, url)
    except LookupError:
        return False
    return True


def text_of(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def when(text: str) -> date:
    return datetime.strptime(" ".join(text.split()), "%d %B %Y").date()


def proper(printed: str) -> str:
    words = printed.split()
    cut = len(words)
    while cut > 1 and words[cut - 1].isupper():
        cut -= 1
    return (
        " ".join([*words[:cut], display(" ".join(words[cut:])).replace(" Of ", " of ")])
        if cut < len(words)
        else printed
    )


def council_name(lad: str, text: str) -> str:
    text = " ".join(text.split()).replace("&", "and")
    text = re.sub(r"\s+Community Council$", "", text)
    return ALIASES.get((lad, text), text)


def declaration(text: str) -> dict:
    places = re.search(r"Declaration of Result for (\d+) Candidates? to be elected", text)
    head = re.search(r"The Highland Council\s+(.+?)\s+Election Date:\s*\w+\s+(\d+\s+\w+\s+\d{4})", text, re.DOTALL)
    body = re.search(r"Number of Votes\s*\n(.*?)\n\s*And that those", text, re.DOTALL)
    candidates = []
    if head and body:
        for line in body.group(1).splitlines():
            if not line.strip():
                continue
            row = re.fullmatch(r"\s*(.+?)\s{2,}(?:(Yes|YES|No|NO|Elected)\s{2,})?(\d+)\s*", line)
            if row is None:
                raise ValueError(f"unreadable candidate line {line!r}")
            candidates.append(
                {
                    "printed": row.group(1).strip(),
                    "votes": int(row.group(3)),
                    "elected": row.group(2) in ("Yes", "YES", "Elected"),
                }
            )
        council, day = head.group(1), head.group(2)
    else:
        head = re.search(r"The Highland Council\s+(.+?)\s+Declaration of Result", text, re.DOTALL)
        dated = re.search(r"Date:\s*\w+\s+(\d+\s+\w+\s+\d{4})", text)
        body = re.search(r"ELECTED \(\d+\)\s*\n(.*?)\n\s*The \d+ elected candidates", text, re.DOTALL)
        if not (head and dated and body):
            raise ValueError("unreadable declaration")
        rows = list(re.finditer(r"((?:[A-Z][A-Z'-]+ )+)([^\n]+?)\s+(\d+)\s+(Not Elected|Elected)", body.group(1)))
        if re.sub(r"\s+", "", "".join(row.group(0) for row in rows)) != re.sub(r"\s+", "", body.group(1)):
            raise ValueError("unreadable candidate lines")
        for row in rows:
            candidates.append(
                {
                    "printed": f"{row.group(2).strip()} {row.group(1).strip()}",
                    "votes": int(row.group(3)),
                    "elected": row.group(4) == "Elected",
                }
            )
        council, day = head.group(1), dated.group(1)
    if not places:
        raise ValueError("no places")

    def figure(label: str) -> int:
        found = re.search(rf"{label}\s+([\d,]+)\s*$", text, re.MULTILINE) or re.search(
            rf"{label.replace(' Returned', '')}\s+([\d,]+)\s+Returned", text
        )
        if found is None:
            raise ValueError(f"no {label}")
        return int(found.group(1).replace(",", ""))

    return {
        "council": council,
        "day": when(day),
        "places": int(places.group(1)),
        "candidates": candidates,
        "electorate": figure("Electorate"),
        "ballots": figure("Total (?:Number|No) of Postal Votes Returned"),
        "rejected": figure(r"Total Rejected Ballot Papers?"),
        "valid": figure("Total Valid Votes"),
    }


def notice(text: str) -> dict:
    head = re.search(r"Election Of Community Councillors for\s+(.+?)\s+NOTICE IS HEREBY GIVEN", text, re.DOTALL)
    day = re.search(r"with effect from 12 noon on \w+\s+(\d+\s+\w+\s+\d{4})", " ".join(text.split()))
    table = re.search(r"\n([^\n]*Forename[^\n]*)\n(.*?)\n\s*\*Where", text, re.DOTALL)
    if not (head and day and table):
        raise ValueError("unreadable notice")
    header = table.group(1)
    surname_first = header.index("Surname") < header.index("Forename")
    total = "Membership" in text[: table.start(2)]
    names, places, left = [], None, None
    for line in table.group(2).splitlines():
        cells = [cell for cell in re.split(r"\s{2,}", line.strip()) if cell]
        if not cells or cells[0].startswith(("Vacancies", "Membership", "remaining")):
            continue
        numbers = []
        while cells and cells[0].isdigit():
            numbers.append(int(cells.pop(0)))
        if numbers:
            places = numbers[-1]
            if len(numbers) != (2 if total else 1):
                raise ValueError(f"unexpected leading figures {numbers}")
        if cells and cells[-1].isdigit():
            left = int(cells.pop())
        if len(cells) != 2:
            raise ValueError(f"unreadable member line {line!r}")
        surname, forename = cells if surname_first else cells[::-1]
        names.append(f"{forename} {surname}")
    if places is None or left is None or places - len(names) != left:
        raise ValueError(f"places {places}, members {len(names)}, and vacancies left {left} do not agree")
    return {"council": head.group(1), "day": when(day.group(1)), "places": places, "members": names}


def parse() -> None:
    rows = []
    for url, lad, kind in pl.read_csv(SOURCES, separator="\t").rows():
        if not _cached(url):
            print(f"not fetched: {url}", file=sys.stderr)
            continue
        text = text_of(url)
        if kind == "declaration":
            read = declaration(text)
            elected = sum(candidate["elected"] for candidate in read["candidates"])
            if elected != min(read["places"], len(read["candidates"])):
                raise ValueError(f"{url}: {elected} elected to {read['places']} places")
            if read["ballots"] != read["valid"] + read["rejected"]:
                raise ValueError(f"{url}: ballots do not add up")
            ranked = sorted(read["candidates"], key=lambda candidate: -candidate["votes"])
            if {c["printed"] for c in ranked[:elected]} != {c["printed"] for c in ranked if c["elected"]}:
                raise ValueError(f"{url}: the elected candidates are not the top polling ones")
            candidates = read["candidates"]
        else:
            read = notice(text)
            candidates = [{"printed": name, "votes": None, "elected": True} for name in read["members"]]
        council = council_name(lad, read["council"])
        for candidate in candidates:
            rows.append(
                {
                    "url": url,
                    "lad": lad,
                    "council": council,
                    "key": f"{lad}-{slug(council)}",
                    "day": read["day"],
                    "places": read["places"],
                    "printed": candidate["printed"],
                    "name": proper(str(candidate["printed"])),
                    "votes": candidate["votes"],
                    "elected": candidate["elected"],
                    "electorate": read.get("electorate"),
                    "ballots": read.get("ballots"),
                    "rejected": read.get("rejected"),
                    "valid": read.get("valid"),
                }
            )
    frame = pl.DataFrame(rows)
    with connect() as conn:
        known = {
            code
            for (code,) in conn.execute(
                "select c.code from area a join area_code c on c.area_id = a.id where a.scheme_code = %s", [SCHEME]
            ).fetchall()
        }
    if missing := sorted(set(frame["key"]) - known):
        raise ValueError(f"councils not in the register: {missing}")
    if clash := frame.group_by("key", "day").agg(pl.col("url").n_unique()).filter(pl.col("url") > 1).rows():
        raise ValueError(f"two sources for one election: {clash}")
    summary = frame.group_by("day").agg(pl.col("key").n_unique()).sort("day").rows()
    print(f"{frame['key'].n_unique()} councils, {frame.height} candidates; elections by day {summary}", file=sys.stderr)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Highland community council elections of 2023 to 2026 under the council's scheme of "
            "establishment: postal ballots from the returning officer's declarations of result, and councils "
            "formed unopposed from the notices of uncontested election, as tacit sets",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        systems = {code: cs.key("gb.electoral_system", code) for code in ("fptp", "block")}
        if any(system in cs.minted for system in systems.values()):
            raise ValueError("publish gb.parliament first")
        schemes = {}
        for lad, (publisher, title, url, _) in COUNCILS.items():
            schemes[lad] = cs.source(publisher, title)
            cs.add("source", id=schemes[lad], kind="official_body", publisher=publisher, title=title, url=url)
        sources: dict[str, UUID] = {}
        for url, lad, kind in pl.read_csv(SOURCES, separator="\t").rows():
            if url not in set(frame["url"]):
                continue
            first = frame.filter(pl.col("url") == url).row(0, named=True)
            label = "Declaration of Result" if kind == "declaration" else "Notice of Uncontested Election"
            title = f"{first['council']} Community Council, {label}, {first['day']:%-d %B %Y}"
            publisher = COUNCILS[lad][0]
            sources[url] = cs.source(publisher, title)
            cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        paths = dict(
            conn.execute(
                """select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
                   where e.scheme = 'gb.polity' and e.value = any(%s)""",
                [sorted(set(frame["key"]))],
            ).fetchall()
        )
        bodies = dict(
            conn.execute(
                """select e.value, n.text from external_id e join name n on n.entity_id = e.entity_id and n.is_preferred
                   where e.scheme = 'gb.body' and e.value = any(%s)""",
                [sorted(set(frame["key"]))],
            ).fetchall()
        )
        people: dict[str, UUID] = {}
        units: set[UUID] = set()
        for (key, day), lines in frame.group_by("key", "day", maintain_order=True):
            assert isinstance(day, date)
            first = lines.row(0, named=True)
            lad, url, places = first["lad"], first["url"], first["places"]
            tacit = first["votes"] is None
            polity = cs.key("gb.polity", key)
            body = cs.key("gb.body", key)
            area = cs.area(SCHEME, key)
            unit = cs.unit(SCHEME, key)
            if unit in cs.minted and unit not in units:
                units.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=SCHEME,
                    code=key,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, first["council"])
            contest_key = f"{day}:cc:{key}"
            election = cs.key("gb.election", contest_key)
            cs.add("election", id=election, polity_id=polity, kind="local", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} {bodies[key]} election")
            election_path = f"{paths[key]}/{day}-local"
            cs.add("slug", path=election_path, entity_id=election)
            cs.add("note", entity_id=election, kind="context", body_md=COUNCILS[lad][3])
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=places,
                round_number=1,
                electoral_system_id=systems["fptp" if places == 1 else "block"],
                default_result_kind="tacit" if tacit else "counted",
            )
            name(contest, f"{bodies[key]}, {day.year}")
            cs.add("slug", path=f"{election_path}/council", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=sources[url])
            cs.add("citation", entity_id=contest, source_id=schemes[lad])
            result_set = cs.key("gb.cc_elections.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=sources[url],
                is_default=True,
                completeness=1,
            )
            if tacit and lines.height < places:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=SHORT.format(elected=lines.height, places=places, left=places - lines.height),
                )
            for number, line in enumerate(lines.iter_rows(named=True), start=1):
                option_key = f"{contest_key}:{number}"
                person_key = f"{key}:{slug(line['name'])}"
                if person_key not in people:
                    people[person_key] = cs.key("gb.cc_elections.person", person_key)
                    cs.add("person", id=people[person_key])
                    name(people[person_key], line["name"])
                option = cs.key("gb.cc_elections.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_label=line["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.cc_elections.candidacy", option_key),
                    person_id=people[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=line["printed"],
                    member_of_party_id=None,
                    is_independent=False,
                )
                if line["votes"] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=0,
                        flags=0,
                        value=line["votes"],
                    )
                if line["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            if tacit:
                continue
            for measure, field in (
                (REGISTERED, "electorate"),
                (BALLOTS, "ballots"),
                (VALID, "valid"),
                (INVALID, "rejected"),
            ):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=first[field],
                )
        return cs.write()
