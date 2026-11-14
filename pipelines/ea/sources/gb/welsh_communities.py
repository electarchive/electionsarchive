import html
import re
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.cc_elections import proper, surname_first
from ea.sources.gb.parish import fold, slug
from ea.store import DATA, connect

SOURCE = "gb.welsh_communities"
VERSION = "1"
WARDS = "gb.parish_ward"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2022, 5, 5)
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
VOTES = 1
COUNCILS = {
    "W06000020": (
        "Torfaen County Borough Council",
        "Community Council Elections 2022",
        "https://www.torfaen.gov.uk/en/about-the-council/elections/elections-results/community-council",
        "torfaen",
    ),
}
WARD_NAMES = {"Grifithstown East": "Griffithstown East", "Grifithstown West": "Griffithstown West"}
PARTIES: dict[str, str | None] = {
    "": None,
    "Independent": "independent",
    "Welsh Labour": "Q9630",
    "Welsh Labour/Llafur Cymru": "Q9630",
    "Welsh Conservative Party Candidate": "Q9626",
}
CONTEXT = (
    "Wales's community and town councils are elected every five years on the day of the principal council "
    "elections, ward by ward where a community is warded, by block vote. Where no more candidates are nominated "
    "than there are seats, those nominated are declared elected without a poll."
)
UNSTATED = (
    "{publisher}'s results list the candidates declared elected unopposed but not how many seats the ward has, "
    "so any seat left unfilled is not recorded."
)


def fetch() -> None:
    for _, _, url, _ in COUNCILS.values():
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        response = httpx.get(url, headers={"User-Agent": BROWSER}, timeout=90, follow_redirects=True)
        response.raise_for_status()
        if response.content.startswith(b"%PDF"):
            suffix, media = ".pdf", "application/pdf"
        else:
            suffix, media = ".html", "text/html"
        raw.store(SOURCE, url, response.content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)


def text_of(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    if path.suffix == ".html":
        return path.read_text()
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def flat(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def torfaen(page: str) -> list[dict]:
    start = page.index("<h2>Community Council Elections 2022")
    section = page[start : page.index("<h2", start + 4)]
    reads: list[dict] = []
    council = ""
    for match in re.finditer(r"<(h3|h4)>(.*?)</\1>(.*?)(?=<h[34]>|$)", section, re.DOTALL):
        level, heading, body = match.group(1), flat(match.group(2)), match.group(3)
        uncontested = re.search(r"\s*-\s*Uncontested$", heading) is not None
        heading = re.sub(r"\s*-\s*Uncontested$", "", heading)
        if level == "h3":
            council = re.sub(r" (?:Town|Community) Council$", "", heading)
            if "<table" not in body:
                continue
            ward = None
        else:
            ward = WARD_NAMES.get(heading, heading)
        rows = [
            [flat(cell) for cell in re.findall(r"<td>(.*?)</td>", row, re.DOTALL)]
            for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL)
        ]
        rows = [row for row in rows if row]
        candidates = []
        for cells in rows:
            if uncontested:
                printed, _, description = cells
                candidates.append(
                    {
                        "printed": printed,
                        "name": proper(printed),
                        "description": description,
                        "votes": None,
                        "elected": True,
                    }
                )
            else:
                printed, description, count = cells
                votes = re.fullmatch(r"(\d+)( \(ELECTED\))?", count)
                if votes is None:
                    raise ValueError(f"Torfaen {council} {ward}: {count}")
                candidates.append(
                    {
                        "printed": printed,
                        "name": surname_first(printed),
                        "description": description,
                        "votes": int(votes.group(1)),
                        "elected": votes.group(2) is not None,
                    }
                )
        turnout = re.search(r"Turnout - (\d+)%", body)
        reads.append(
            {
                "council": council,
                "ward": ward,
                "places": None if uncontested else sum(1 for candidate in candidates if candidate["elected"]),
                "turnout": int(turnout.group(1)) if turnout else None,
                "candidates": candidates,
            }
        )
    return reads


READERS = {"torfaen": torfaen}


def parse() -> None:
    with connect() as conn:
        councils = conn.execute(
            """select lp.value, n.text, be.value, a.scheme_code, c.code
               from external_id be join body b on b.id = be.entity_id
               join polity_parent pp on pp.polity_id = b.polity_id
               join external_id lp on lp.entity_id = pp.parent_id and lp.scheme = 'gb.polity'
               join polity_territory pt on pt.polity_id = b.polity_id and upper_inf(pt.valid)
               join area a on a.id = pt.area_id join area_code c on c.area_id = a.id
               join name n on n.entity_id = b.polity_id and n.is_preferred and n.lang = 'en'
               where be.scheme = 'gb.body' and lp.value = any(%s) and b.kind = 'council'""",
            [sorted(COUNCILS)],
        ).fetchall()
    known = {(lad, fold(name)): (key, scheme, code) for lad, name, key, scheme, code in councils}
    rows = []
    for lad, (_, _, url, reader) in COUNCILS.items():
        for read in READERS[reader](text_of(url)):
            found = known.get((lad, fold(read["council"])))
            if found is None:
                raise ValueError(f"{lad} {read['council']}: no council in the register")
            key, scheme, code = found
            for number, candidate in enumerate(read["candidates"], start=1):
                if candidate["description"] not in PARTIES:
                    raise ValueError(f"unknown description: {candidate['description']}")
                rows.append(
                    {
                        "lad": lad,
                        "url": url,
                        "council": read["council"],
                        "key": key,
                        "scheme": scheme,
                        "code": code,
                        "ward": read["ward"],
                        "places": read["places"],
                        "turnout": read["turnout"],
                        "number": number,
                        **candidate,
                    }
                )
    frame = pl.DataFrame(rows, infer_schema_length=None)
    if clash := frame.group_by("key", "ward").agg(pl.col("url").n_unique()).filter(pl.col("url") > 1).rows():
        raise ValueError(f"two sources for one ward: {clash}")
    contests = frame.select("key", "ward").unique().height
    uncontested = frame.filter(pl.col("votes").is_null()).select("key", "ward").unique().height
    print(
        f"{frame['key'].n_unique()} councils, {contests} contests ({uncontested} uncontested), "
        f"{frame.height} candidates",
        file=sys.stderr,
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Welsh community and town council elections of 5 May 2022, ward by ward, from the principal "
            "councils' published results: polls with votes, and wards filled unopposed as tacit sets",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        systems = {code: cs.key("gb.electoral_system", code) for code in ("fptp", "block")}
        if any(system in cs.minted for system in systems.values()):
            raise ValueError("publish gb.parliament first")
        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = %s", [WARDS]).fetchone() is None:
            cs.add(
                "scheme", code=WARDS, polity_id=gb, kind="electoral_district", rank=5, noun="parish or community ward"
            )
        sources: dict[str, UUID] = {}
        for publisher, title, url, _ in COUNCILS.values():
            if url not in set(frame["url"]):
                continue
            sources[url] = cs.source(publisher, title)
            cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        keys = sorted(set(frame["key"]))
        paths = dict(
            conn.execute(
                """select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
                   where e.scheme = 'gb.polity' and e.value = any(%s)""",
                [keys],
            ).fetchall()
        )
        bodies = dict(
            conn.execute(
                """select e.value, n.text from external_id e join name n on n.entity_id = e.entity_id and n.is_preferred
                   where e.scheme = 'gb.body' and e.value = any(%s)""",
                [keys],
            ).fetchall()
        )
        parties: dict[str, UUID] = {}

        def party_of(description: str) -> UUID | None:
            target = PARTIES[description]
            if target is None or target == "independent":
                return None
            if target not in parties:
                parties[target] = cs.key("wikidata", target)
                if parties[target] in cs.minted:
                    raise ValueError(f"{target} is not a party in the archive")
            return parties[target]

        people: dict[str, UUID] = {}
        elections: set[str] = set()
        for (key, ward), lines in frame.group_by("key", "ward", maintain_order=True):
            assert isinstance(key, str)
            first = lines.row(0, named=True)
            places = first["places"]
            tacit = first["votes"] is None
            polity = cs.key("gb.polity", key)
            council_area = cs.area(first["scheme"], first["code"])
            election_key = f"{DAY}:wcc:{key}"
            election = cs.key("gb.election", election_key)
            election_path = f"{paths[key]}/{DAY}-local"
            if key not in elections:
                elections.add(key)
                cs.add(
                    "election", id=election, polity_id=polity, kind="local", date_from=DAY, date_to=DAY, status="held"
                )
                name(election, f"{DAY.year} {bodies[key]} election")
                cs.add("slug", path=election_path, entity_id=election)
                cs.add("note", entity_id=election, kind="context", body_md=CONTEXT)
            if ward is None:
                scheme, code, area = first["scheme"], first["code"], council_area
            else:
                scheme, code = WARDS, f"{key}:{slug(ward)}"
                area = cs.area(WARDS, code)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code=WARDS)
                    cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=area, parent_id=council_area, valid_from=None, valid_to=None)
                name(area, ward)
            unit = cs.unit(scheme, code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, ward or first["council"])
            contest_key = f"{election_key}:{slug(ward) if ward else 'council'}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=cs.key("gb.body", key),
                district_area_id=area,
                magnitude=places,
                round_number=1,
                electoral_system_id=systems["fptp" if places == 1 else "block"],
                default_result_kind="tacit" if tacit else "counted",
            )
            name(contest, f"{bodies[key]}, {ward} ward, {DAY.year}" if ward else f"{bodies[key]}, {DAY.year}")
            cs.add("slug", path=f"{election_path}/{slug(ward) if ward else 'council'}", entity_id=contest)
            source = sources[first["url"]]
            cs.add("citation", entity_id=contest, source_id=source)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            publisher = COUNCILS[first["lad"]][0]
            if tacit and places is None:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNSTATED.format(publisher=publisher))
            if first["turnout"] is not None:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{publisher} gives turnout as {first['turnout']}% and prints no ballot or electorate counts.",
                )
            for line in lines.iter_rows(named=True):
                option_key = f"{contest_key}:{line['number']}"
                person_key = f"{key}:{slug(line['name'])}"
                if person_key not in people:
                    people[person_key] = cs.key(f"{SOURCE}.person", person_key)
                    cs.add("person", id=people[person_key])
                    name(people[person_key], line["name"])
                party = party_of(line["description"])
                option = cs.key(f"{SOURCE}.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=line["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", option_key),
                    person_id=people[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=line["printed"],
                    member_of_party_id=party,
                    is_independent=PARTIES[line["description"]] == "independent",
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
        return cs.write()
