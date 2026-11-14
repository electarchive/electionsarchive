import html
import re
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urljoin
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
VOTES, REGISTERED, INVALID = 1, 2, 5
COUNCILS = {
    "W06000020": (
        "Torfaen County Borough Council",
        "Community Council Elections 2022",
        "https://www.torfaen.gov.uk/en/about-the-council/elections/elections-results/community-council",
        "torfaen",
    ),
    "W06000010": (
        "Carmarthenshire County Council",
        "Community Councillor Results, Local Elections 2022",
        "https://www.carmarthenshire.gov.wales/council-democracy/elections-voting/local-elections-2022/community-councillor-results/",
        "carmarthenshire",
    ),
    "W06000003": (
        "Conwy County Borough Council",
        "Election results by community area, Local Government Elections, 5 May 2022",
        "https://modgovpub-eng.secure.conwy.gov.uk/mgElectionElectionAreaResults.aspx?Page=all&EID=104&RPID=20212970",
        "moderngov",
    ),
    "W06000021": (
        "Monmouthshire County Council",
        "Election results by community area, Community Council Elections, 5 May 2022",
        "https://democracy.monmouthshire.gov.uk/mgElectionElectionAreaResults.aspx?EID=11",
        "moderngov",
    ),
}
ALIASES = {
    ("W06000010", "Cwarter Bach"): "Quarter Bach",
    ("W06000010", "Dyffryn Cennen"): "Cyngor Bro Dyffryn Cennen",
    ("W06000010", "Llanddowror and Llanmiloe"): "Llanddowror",
    ("W06000010", "Llansteffan & Llanybri"): "Llansteffan",
    ("W06000003", "Bay of Colwyn"): "Colwyn Bay",
    ("W06000003", "Towyn and Kinmel Bay"): "Kinmel Bay and Towyn",
    ("W06000021", "Llanfoist"): "Llanfoist Fawr",
}
UNREGISTERED = {("W06000010", "Trelech a'r Betws"), ("W06000003", "Betws-y-Coed"), ("W06000021", "Llangybi Fawr")}
WARD_NAMES = {
    "Abersychan and Cwmavon": "Abersychan & Cwmavon",
    "Grifithstown East": "Griffithstown East",
    "Grifithstown West": "Griffithstown West",
}
PARTIES: dict[str, str | None] = {
    "": None,
    "Independent": "independent",
    "Welsh Labour": "Q9630",
    "Welsh Labour/Llafur Cymru": "Q9630",
    "Welsh Conservative Party Candidate": "Q9626",
    "Plaid Cymru – The Party of Wales": "Q10691",
    "Welsh Labour / Llafur Cymru": "Q9630",
    "Independent / Annibynnol": "independent",
    "Welsh Liberal Democrats / Democratiaid Rhyddfrydol Cymru": "Q9624",
    "Reform UK": "Q104721252",
    "Gwlad - Wales Can be Better": "Q75321093",
    "Ceidwadwyr Cymreig / Welsh Conservatives": "Q9626",
    "Annibynnol": "independent",
    "Annibynnol / Independent": "independent",
    "Plaid Cymru - The Party of Wales": "Q10691",
    "Welsh Conservative Party Candidate / Ymgeisydd Plaid Geidwadol Cymru": "Q9626",
    "Labour and Co-operative Party / Llafur a'r Blaid Gydweithredol": "Q9630",
    "Wales Green Party/Plaid Werdd Cymru": "Q3499324",
    "Welsh Conservative Party": "Q9626",
    "Liberal Democrats": "Q9624",
    "Welsh Liberal Democrats": "Q9624",
    "Welsh Labour/Llafur Cymr": "Q9630",
}
BY_CONTEXT = (
    "A by-election to fill a casual vacancy on a Welsh community or town council, held between the five-yearly "
    "elections, by block vote where a ward has more than one seat. Where only as many candidates are nominated as "
    "there are vacancies, those nominated are declared elected without a poll."
)
NO_WARD = (
    "Torfaen County Borough Council's page names no ward for this by-election, so the contest is recorded against "
    "the whole council area."
)
CAVEATS = {
    "Abergavenny (Cantref) - results": (
        "Monmouthshire County Council's page lists Bryony Nicholson three times: twice with 432 votes and no "
        "description, and once as Welsh Labour with no votes. She is stored once, with 432 votes. The page's total of "
        "2,827 votes counts her 432 twice; the six candidates' votes sum to 2,395."
    ),
}
CONTEXT = (
    "Wales's community and town councils are elected every five years on the day of the principal council "
    "elections, ward by ward where a community is warded, by block vote. Where no more candidates are nominated "
    "than there are seats, those nominated are declared elected without a poll."
)
SHORT = "{publisher}'s results list {elected} candidates declared elected unopposed to {places} seats, leaving {left} unfilled."
UNSTATED = (
    "{publisher}'s results list the candidates declared elected unopposed but not how many seats the ward has, "
    "so any seat left unfilled is not recorded."
)


def areas(url: str) -> list[str]:
    links = re.findall(r'href="(mgElectionAreaResults\.aspx[^"]+)"', raw.cached(SOURCE, url).path.read_text())
    return [urljoin(url, html.unescape(link)) for link in dict.fromkeys(links)]


def fetch() -> None:
    for _, _, url, reader in COUNCILS.values():
        store(url)
        if reader == "moderngov":
            for page in areas(url):
                if store(page):
                    time.sleep(1)


def store(url: str) -> bool:
    try:
        raw.cached(SOURCE, url)
        return False
    except LookupError:
        pass
    response = httpx.get(url, headers={"User-Agent": BROWSER}, timeout=90, follow_redirects=True)
    response.raise_for_status()
    if response.content.startswith(b"%PDF"):
        suffix, media = ".pdf", "application/pdf"
    else:
        suffix, media = ".html", "text/html"
    raw.store(SOURCE, url, response.content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)
    return True


def text_of(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    if path.suffix == ".html":
        return path.read_text()
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def found(pattern: str, text: str, flags: int = 0) -> re.Match[str]:
    match = re.search(pattern, text, flags)
    if match is None:
        raise ValueError(f"{pattern} not found in {text[:200]}")
    return match


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
        candidates: list[dict] = []
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
                "turnout": turnout.group(1) if turnout else None,
                "candidates": candidates,
            }
        )
    return reads


def torfaen_by_elections(page: str) -> list[dict]:
    reads: list[dict] = []
    end = page.index("<h2>Community Council Elections 2022")
    sections = re.findall(r"<h2>([^<]*?\d{4})</h2>(.*?)(?=<h2|$)", page[:end], re.DOTALL)
    for title, body in sections:
        title = html.unescape(title)
        council, when = found(r"^(.+?) (?:Town|Community) Councils? Elections? - (.+)$", title).groups()
        day = datetime.strptime(when, "%d %B %Y").date()
        for heading, part in re.findall(r"(?:<h3>(.*?)</h3>)?(.*?)(?=<h3>|$)", body, re.DOTALL):
            if "<table" not in part:
                continue
            ward = re.sub(r"\s*-\s*Uncontested$", "", flat(heading)) if heading else None
            uncontested = heading != "" and flat(heading).endswith("Uncontested")
            ward = re.sub(r"\s+Ward$", "", ward) if ward else None
            ward = WARD_NAMES.get(ward, ward)
            candidates: list[dict] = []
            for row in re.findall(r"<tr>(.*?)</tr>", part, re.DOTALL):
                cells = [flat(cell) for cell in re.findall(r"<td>(.*?)</td>", row, re.DOTALL)]
                if not cells:
                    continue
                printed = cells[0]
                if uncontested:
                    candidates.append(
                        {
                            "printed": printed,
                            "name": proper(printed),
                            "description": cells[2],
                            "votes": None,
                            "elected": True,
                        }
                    )
                    continue
                votes = re.fullmatch(r"(\d+)( \(ELECTED\))?", cells[2])
                if votes is None:
                    raise ValueError(f"Torfaen {title}: {cells[2]}")
                candidates.append(
                    {
                        "printed": printed,
                        "name": surname_first(printed),
                        "description": cells[1],
                        "votes": int(votes.group(1)),
                        "elected": votes.group(2) is not None,
                    }
                )
            reads.append(
                {
                    "council": re.sub(r"\s+", " ", council),
                    "ward": ward,
                    "day": day,
                    "by": True,
                    "caveat": None if ward else NO_WARD,
                    "places": None if uncontested else sum(1 for candidate in candidates if candidate["elected"]),
                    "turnout": None,
                    "candidates": candidates,
                }
            )
    return reads


def carmarthenshire(page: str) -> list[dict]:
    reads = []
    for panel in page.split('<div class="panel panel-default">')[1:]:
        title = flat(found(r'<h2 class="panel-title">(.*?)</h2>', panel, re.DOTALL).group(1))
        council, ward = found(r"^(.+?)(?: \((.+?)(?: Ward)?\))?$", title).groups()
        summary = flat(panel[panel.index("<h2>Summary") :].split("<h2>Downloads")[0])
        places = int(found(r"Number of Councillors to be elected : (\d+)", summary).group(1))
        uncontested = "This ward is uncontested" in summary
        table = found(r"<tbody>(.*?)</tbody>", panel, re.DOTALL).group(1)
        rows = [
            [flat(cell) for cell in re.findall(r"<td>(.*?)</td>", row, re.DOTALL)]
            for row in re.findall(r"<tr>(.*?)</tr>", table, re.DOTALL)
        ]
        elected = set()
        if not uncontested:
            block = panel[panel.index("<strong>Elected:</strong>") :].split("</p>")[0]
            elected = {flat(entry).split("/")[0].strip() for entry in block.split("<br />")[1:] if flat(entry)}
        candidates: list[dict] = []
        for cells in rows:
            if not cells[0]:
                continue
            printed, description = cells[0], cells[1]
            candidates.append(
                {
                    "printed": printed,
                    "name": surname_first(printed),
                    "description": description,
                    "votes": None if uncontested else int(cells[2]),
                    "elected": uncontested or printed in elected,
                }
            )
        if not uncontested:
            total = int(found(r"Total votes: (\d+)", summary).group(1))
            if total != sum(int(candidate["votes"]) for candidate in candidates) or len(elected) != places:
                raise ValueError(f"Carmarthenshire {title}: totals or elected do not agree")
            if elected - {candidate["printed"] for candidate in candidates}:
                raise ValueError(f"Carmarthenshire {title}: elected not among candidates")
        turnout = re.search(r"Turnout : ([\d.]+)%", summary)
        reads.append(
            {
                "council": council,
                "ward": ward,
                "places": places,
                "turnout": turnout.group(1) if turnout else None,
                "candidates": candidates,
            }
        )
    return reads


def moderngov(url: str) -> list[dict]:
    reads = []
    for page_url in areas(url):
        page = re.sub(r"<script\b.*?</script>", " ", text_of(page_url), flags=re.DOTALL)
        if "No election candidates have been specified for this area" in page:
            continue
        caption = flat(found(r'<caption\s+class="mgSectionTitle"\s*>(.*?)</caption>', page, re.DOTALL).group(1))
        english = list(re.finditer(r"/\s*([^/(]+?) (?:Town|Community|City) Counci?l?\b", caption))
        council = english[-1].group(1) if english else found(r"^(.+?)(?: \(| - results$)", caption).group(1)
        ward = re.search(r"\(([^()]+?)\)\s*- results$", caption[english[-1].end() if english else 0 :])
        ward = re.sub(r"\s+[Ww]ard$", "", ward.group(1).strip()) if ward else None
        table = page[page.index('class="mgStatsTable') :].split("</table>")[0]
        candidates: list[dict] = []
        rows = [
            [flat(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            for row in re.findall(r"<tr\s+class=\"mgTable(?:Odd|Even)Row\"\s*>(.*?)</tr>", table, re.DOTALL)
        ]
        voted = any(row[2] not in ("No contest", "") for row in rows)
        described = {row[0]: row[1] for row in rows if row[1]}
        seen = set()
        for printed, _, count, _, outcome in rows:
            if printed in seen or (voted and count == ""):
                continue
            seen.add(printed)
            candidates.append(
                {
                    "printed": printed,
                    "name": printed,
                    "description": described.get(printed, ""),
                    "votes": None if count in ("No contest", "") else int(count.replace(",", "")),
                    "elected": outcome == "Elected" or (count == "" and outcome == ""),
                }
            )
        summary = flat(page[page.index("Voting Summary") :].split("</table>")[0]) if "Voting Summary" in page else ""

        def figure(label: str) -> int | None:
            found = re.search(rf"{label} ([\d,]+)", summary)
            return int(found.group(1).replace(",", "")) if found else None

        places = figure("Seats")
        if candidates[0]["votes"] is not None:
            if caption not in CAVEATS and figure("Total votes") != sum(int(c["votes"]) for c in candidates):
                raise ValueError(f"{caption}: total votes do not agree")
            if places != sum(1 for candidate in candidates if candidate["elected"]):
                raise ValueError(f"{caption}: seats and elected do not agree")
        turnout = re.search(r"Turnout ([\d.]+)%", summary)
        reads.append(
            {
                "council": council,
                "ward": ward,
                "places": places,
                "turnout": turnout.group(1) if turnout else None,
                "electorate": figure("Electorate"),
                "counted": figure("Number of ballot papers included in the count"),
                "issued": figure("Number of ballot papers issued"),
                "rejected": figure("Number of ballot papers rejected"),
                "url": page_url,
                "caveat": CAVEATS.get(caption),
                "candidates": candidates,
            }
        )
    return reads


READERS = {
    "torfaen": lambda url: torfaen(text_of(url)) + torfaen_by_elections(text_of(url)),
    "carmarthenshire": lambda url: carmarthenshire(text_of(url)),
    "moderngov": moderngov,
}


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
    unknown = set()
    for lad, (_, _, url, reader) in COUNCILS.items():
        for read in READERS[reader](url):
            found = known.get((lad, fold(ALIASES.get((lad, read["council"]), read["council"]))))
            if (lad, read["council"]) in UNREGISTERED:
                continue
            if found is None:
                unknown.add((lad, read["council"]))
                continue
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
                        "day": read.get("day", DAY),
                        "by": read.get("by", False),
                        "places": read["places"],
                        "turnout": read["turnout"],
                        "electorate": read.get("electorate"),
                        "counted": read.get("counted"),
                        "issued": read.get("issued"),
                        "rejected": read.get("rejected"),
                        "page": read.get("url", url),
                        "caveat": read.get("caveat"),
                        "number": number,
                        **candidate,
                    }
                )
    if unknown:
        raise ValueError(f"no council in the register: {sorted(unknown)}")
    frame = pl.DataFrame(rows, infer_schema_length=None)
    if clash := frame.group_by("key", "ward", "day").agg(pl.col("url").n_unique()).filter(pl.col("url") > 1).rows():
        raise ValueError(f"two sources for one ward: {clash}")
    contests = frame.select("key", "ward", "day").unique().height
    uncontested = frame.filter(pl.col("votes").is_null()).select("key", "ward", "day").unique().height
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
            summary="Welsh community and town council elections of 5 May 2022 and later by-elections, ward by ward, from "
            "the principal councils' published results: polls with votes, and wards filled unopposed as tacit sets",
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
            for page in frame.filter(pl.col("url") == url)["page"].unique().sort():
                cs.artifact(raw.cached(SOURCE, page), sources[url])
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
        added: set[UUID] = set()
        for (key, ward, day), lines in frame.group_by("key", "ward", "day", maintain_order=True):
            assert isinstance(key, str)
            first = lines.row(0, named=True)
            by = first["by"]
            places = first["places"]
            tacit = first["votes"] is None
            polity = cs.key("gb.polity", key)
            council_area = cs.area(first["scheme"], first["code"])
            election_key = f"{day}:wcc:{key}"
            election = cs.key("gb.election", election_key)
            election_path = f"{paths[key]}/{day}-local"
            if election_key not in elections:
                elections.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind="by_election" if by else "local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    election,
                    f"{bodies[key]} by-election, {day.day} {day:%B %Y}" if by else f"{day.year} {bodies[key]} election",
                )
                cs.add("slug", path=election_path, entity_id=election)
                cs.add("note", entity_id=election, kind="context", body_md=BY_CONTEXT if by else CONTEXT)
            if ward is None:
                scheme, code, area = first["scheme"], first["code"], council_area
            else:
                scheme, code = WARDS, f"{key}:{slug(ward)}"
                area = cs.area(WARDS, code)
                if area in cs.minted and area not in added:
                    added.add(area)
                    cs.add("area", id=area, scheme_code=WARDS)
                    cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=area, parent_id=council_area, valid_from=None, valid_to=None)
                    name(area, ward)
            unit = cs.unit(scheme, code)
            if unit in cs.minted and unit not in added:
                added.add(unit)
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
            label = f"by-election, {day.day} {day:%B %Y}" if by else str(day.year)
            name(contest, f"{bodies[key]}, {ward} ward, {label}" if ward else f"{bodies[key]}, {label}")
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
            elif tacit and lines.height < places:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=SHORT.format(
                        publisher=publisher, elected=lines.height, places=places, left=places - lines.height
                    ),
                )
            if first["caveat"]:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=first["caveat"])
            if first["counted"] is not None:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{publisher} prints {first['counted']:,} ballot papers included in the count and a "
                    f"turnout of {first['turnout']}%.",
                )
            elif first["issued"] is not None:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{publisher} prints {first['issued']:,} ballot papers issued.",
                )
            elif first["turnout"] is not None:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{publisher} gives turnout as {first['turnout']}% and prints no ballot or electorate counts.",
                )
            if not tacit:
                for measure, field in ((REGISTERED, "electorate"), (INVALID, "rejected")):
                    if first[field] is not None:
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
