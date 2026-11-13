import html
import re
import subprocess
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import display, foreign_preferred
from ea.sources.gb import leh
from ea.store import DATA, connect

SOURCE = "gb.welsh_councils"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual"
DAY = date(1999, 5, 6)
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
SWANSEA = "W06000011"
MONMOUTHSHIRE = "W06000021"
SWANSEA_FILE = "manual:MF-681/swansea-1999-all.html"
MONMOUTHSHIRE_FILE = "manual:MF-684/1999_Election_Results_-_County_Council.pdf"
COUNCILS = {
    SWANSEA: {
        "file": SWANSEA_FILE,
        "publisher": "City and County of Swansea",
        "title": "Local Government Election, 6 May 1999: results by ward",
        "url": "https://democracy.swansea.gov.uk/mgElectionResults.aspx?ID=18",
        "notes": "The council's committee site (modern.gov) gives each ward's candidates, the party it files them under, "
        "votes, the elected candidates, the seats, the total of votes cast for candidates, and the electorate; wards "
        "won without a poll show No contest. The pages were saved by hand, because the site refuses scripted requests.",
    },
    MONMOUTHSHIRE: {
        "file": MONMOUTHSHIRE_FILE,
        "publisher": "Monmouthshire County Council",
        "title": "County Council Election, 6 May 1999: declarations of result of poll",
        "url": "https://www.monmouthshire.gov.uk/app/uploads/2013/06/1999_Election_Results_-_County_Council.pdf",
        "notes": "The returning officer's bilingual declaration of result for each of the 37 electoral divisions, one "
        "page each, with candidates, descriptions, votes, the elected candidates, rejected ballots, vacant seats, "
        "electorate, and ballot papers issued. Divisions won without a poll print the candidate with no votes. The "
        "council's archive page links the file, but the link is broken, so it was read from a saved copy of the same "
        "address.",
    },
}
PARTIES = {
    "Labour": "Labour",
    "The Labour Party": "Labour",
    "The Labour Party Candidate": "Labour",
    "The Labour Party Candidte": "Labour",
    "Conservative": "Conservative",
    "Conservative Party": "Conservative",
    "Conservative Party Candidate": "Conservative",
    "Welsh Conservative Party": "Conservative",
    "Welsh Conservative Party Candidate": "Conservative",
    "The Welsh Conservative Party": "Conservative",
    "The Welsh Conservative Party Candidate": "Conservative",
    "The Welsh Conservative Candidate": "Conservative",
    "Liberal Democrat": "Liberal Democrats",
    "Liberal Democrats": "Liberal Democrats",
    "Plaid Cymru": "Plaid Cymru",
    "Plaid Cymru The Party of Wales": "Plaid Cymru",
    "Green Party": "Green Party",
}
INDEPENDENT = {
    "Independent",
    "Independant",
    "Independent Candidate",
    "Independent Tennant",
    "Uplands Neighbourhood Independent",
    "Independents@Swansea",
}
SWANSEA_SEATS = {
    "Labour": 47,
    "Liberal Democrats": 10,
    "Independent": 8,
    "Conservative": 4,
    "Plaid Cymru": 2,
    "The People's Representative": 1,
}


def fetch() -> None:
    for council in COUNCILS.values():
        file = INBOX / council["file"].removeprefix("manual:")
        pdf = file.suffix == ".pdf"
        raw.store(
            SOURCE,
            council["file"],
            file.read_bytes(),
            suffix=file.suffix,
            retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
            media_type="application/pdf" if pdf else "text/html",
        )


def found(pattern: str, content: str, flags: int = 0) -> re.Match[str]:
    match = re.search(pattern, content, flags)
    if match is None:
        raise ValueError(f"no match for {pattern}")
    return match


def text(cell: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def swansea() -> list[dict]:
    content = Path(raw.cached(SOURCE, SWANSEA_FILE).path).read_text(encoding="utf-8")
    rows = []
    for url, page in re.findall(r"<!-- URL: (\S+) --(.*?)(?=<!-- URL: |\Z)", content, re.DOTALL):
        if "mgElectionAreaResults" not in url:
            continue
        ward = text(found(r"<caption[^>]*>(.*?) - results</caption>", page, re.DOTALL)[1])
        table = found(r'summary="Table of results for Wards[^"]*">(.*?)</table>', page, re.DOTALL)[1]
        voting = re.search(r'summary="Voting summary table">(.*?)</table>', page, re.DOTALL)
        summary = {
            text(label): int(text(value))
            for label, value in re.findall(
                r"<td[^>]*>([^<]+)</td>\s*<td[^>]*mgAlignRightCell[^>]*>\s*(\d+)\s*</td>",
                voting.group(1) if voting else "",
            )
        }
        lines = [
            [text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", line, re.DOTALL)]
            for line in re.findall(r"<tr\s+class=\"mgTable(?:Odd|Even)Row\"\s*>(.*?)</tr>", table, re.DOTALL)
        ]
        for number, cells in enumerate(lines, 1):
            name, party, votes, *rest = cells
            outcome = rest[-1]
            rows.append(
                {
                    "la_code": SWANSEA,
                    "ward": ward,
                    "locator": url,
                    "page": None,
                    "number": number,
                    "surname": None,
                    "name": name,
                    "known_as": None,
                    "description": party or None,
                    "votes": None if votes == "No contest" else int(votes),
                    "elected": outcome == "Elected",
                    "seats": summary.get("Seats", sum(line[-1] == "Elected" for line in lines)),
                    "registered": summary.get("Electorate"),
                    "ballots": None,
                    "rejected": None,
                    "total": summary.get("Total votes"),
                }
            )
    return rows


def pdf_words(path: str, page: int) -> list[tuple[float, float, str]]:
    out = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", path, "-"], capture_output=True, text=True, check=True
    ).stdout
    return [
        (float(x), float(y), html.unescape(word))
        for x, y, word in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)</word>', out)
    ]


def monmouthshire() -> list[dict]:
    path = str(raw.cached(SOURCE, MONMOUTHSHIRE_FILE).path)
    pages = int(
        found(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", path], capture_output=True, text=True, check=True).stdout)[
            1
        ]
    )
    rows = []
    for page in range(1, pages + 1):
        layout = subprocess.run(
            ["pdftotext", "-layout", "-f", str(page), "-l", str(page), path, "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        division = found(r"^\s*(.+?) Electoral Division", layout, re.MULTILINE)[1].strip()
        figures = found(
            r"Vacant Seats/Seddau Gwag :(\d+) Electorate/Etholaeth :(\d+) Papers Issued/Papurau a Ddosbarthwyd :(\d*)",
            layout,
        )
        rejected = re.search(r"TOTAL REJECTED / CYFANSWM\s+(\d+)", layout)
        words = pdf_words(path, page)
        top = max(y for x, y, word in words if word == "Disgrifiad") + 8
        bottom = min(y for x, y, word in words if word == "And" and y > top)
        lines: list[list[tuple[float, float, str]]] = []
        for word in sorted((w for w in words if top < w[1] < bottom), key=lambda w: (w[1], w[0])):
            if lines and word[1] - lines[-1][0][1] < 6:
                lines[-1].append(word)
            else:
                lines.append([word])
        number = 0
        for line in lines:
            line.sort()
            surname = " ".join(word for x, y, word in line if x < 165)
            forenames = " ".join(word for x, y, word in line if 165 <= x < 275)
            description = " ".join(word for x, y, word in line if 275 <= x < 490)
            votes = [word for x, y, word in line if 490 <= x and word.isdigit()]
            elected = any(word == "(E)" for x, y, word in line)
            if not surname:
                raise ValueError(f"Monmouthshire page {page}: line without a surname: {line}")
            if rows and rows[-1]["page"] == page and not (forenames or description or votes or elected):
                wrapped = rows[-1]["known_as"] if rows[-1]["surname"] is None else None
                if rows[-1]["surname"] is not None:
                    raise ValueError(f"Monmouthshire page {page}: stray line {surname}")
                rows[-1]["known_as"] = f"{wrapped} {display(surname)}" if wrapped else display(surname)
                continue
            number += 1
            known = re.fullmatch(r"commonly known as ?(.*)", surname)
            rows.append(
                {
                    "la_code": MONMOUTHSHIRE,
                    "ward": division,
                    "locator": MONMOUTHSHIRE_FILE,
                    "page": page,
                    "number": number,
                    "surname": None if known else display(surname),
                    "name": forenames if known else f"{forenames} {display(surname)}",
                    "known_as": (display(known[1]) or None) if known else None,
                    "description": description or None,
                    "votes": int(votes[0]) if votes else None,
                    "elected": elected,
                    "seats": int(figures[1]),
                    "registered": int(figures[2]),
                    "ballots": int(figures[3]) if figures[3] else None,
                    "rejected": int(rejected[1]) if rejected else None,
                    "total": None,
                }
            )
    return rows


def parse() -> None:
    frame = pl.DataFrame(swansea() + monmouthshire(), infer_schema_length=None)
    for (code, ward), lines in frame.group_by("la_code", "ward"):
        votes = lines["votes"].drop_nulls()
        if len(votes) not in (0, len(lines)):
            raise ValueError(f"{code} {ward}: votes for some candidates only")
        if len(votes) == 0:
            if len(lines) != lines["seats"][0]:
                raise ValueError(f"{code} {ward}: {len(lines)} unopposed candidates for {lines['seats'][0]} seats")
            continue
        if int(lines["elected"].sum()) != lines["seats"][0]:
            raise ValueError(f"{code} {ward}: {int(lines['elected'].sum())} elected for {lines['seats'][0]} seats")
        floor = min(lines.filter(pl.col("elected"))["votes"].to_list())
        if max(lines.filter(~pl.col("elected"))["votes"].to_list(), default=0) > floor:
            print(f"{code} {ward}: a candidate not elected outpolls one elected")
        if lines["total"][0] is not None and lines["total"][0] != votes.sum():
            raise ValueError(f"{code} {ward}: votes sum to {votes.sum()}, page total {lines['total'][0]}")
    swansea_seats = Counter(
        row["description"]
        for row in frame.filter((pl.col("la_code") == SWANSEA) & pl.col("elected")).iter_rows(named=True)
    )
    if dict(swansea_seats) != SWANSEA_SEATS:
        raise ValueError(f"Swansea seats by party {dict(swansea_seats)} differ from the council's summary")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    for code, lines in frame.group_by("la_code"):
        wards = lines["ward"].n_unique()
        print(f"{code[0]}: {wards} wards, {lines.height} candidates, {int(lines['elected'].sum())} elected")


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Swansea and Monmouthshire council elections of 6 May 1999 by ward, from the councils' results",
            polity="GB",
        )
        outranked = foreign_preferred(conn, cs, SOURCE)

        def name(entity: UUID, label: str) -> None:
            if entity in outranked and entity not in cs.minted:
                return
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("publish gb.umit and gb.parliament first")
        known: dict[str, UUID] = {}
        for label, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB'"
            " and not exists (select from entity_merge m where m.entity_id = p.id) order by p.id desc"
        ).fetchall():
            known[leh.fold(label)] = id
        parties = {
            label: cs.key(*leh.RESOLVED[label]) if label in leh.RESOLVED else known[leh.fold(label)]
            for label in set(PARTIES.values())
        }
        if cs.minted & set(parties.values()):
            raise ValueError("a party this pipeline links is not in the archive")

        sources: dict[str, UUID] = {}
        for code, council in COUNCILS.items():
            sources[code] = cs.source(council["publisher"], council["title"])
            cs.add(
                "source",
                id=sources[code],
                kind="official_body",
                publisher=council["publisher"],
                title=council["title"],
                url=council["url"],
                license="Open Government Licence v3.0",
                license_url=OGL,
                attribution="Contains public sector information licensed under the Open Government Licence v3.0.",
                notes=council["notes"],
            )
            cs.artifact(raw.cached(SOURCE, council["file"]), sources[code])

        national = cs.key("gb.election", f"{DAY}:local:gb")
        if national in cs.minted:
            cs.add(
                "election",
                id=national,
                polity_id=gb,
                kind="local",
                date_from=DAY,
                date_to=DAY,
                status="held",
            )
            name(national, f"{DAY.year} local elections in England and Wales")
            cs.add("slug", path=f"gb/{DAY}-local", entity_id=national)

        for (code,), council_lines in candidates.group_by("la_code", maintain_order=True):
            area = cs.area("gb.lad", code)
            polity = cs.key("gb.polity", code)
            body = cs.key("gb.body", code)
            if cs.minted & {area, polity, body}:
                raise ValueError(f"{code}: council not in the archive")
            path, council_name = conn.execute(
                "select (select path from slug where entity_id = %s order by is_current desc limit 1),"
                " (select text from name where entity_id = %s and is_preferred limit 1)",
                [polity, body],
            ).fetchall()[0]
            election = cs.key("gb.election", f"{DAY}:local:{code}")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=national,
                    kind="local",
                    date_from=DAY,
                    date_to=DAY,
                    status="held",
                )
                name(election, f"{DAY.year} {council_name} election")
                cs.add("slug", path=f"{path}/{DAY}-local", entity_id=election)
            noun = "division" if code == MONMOUTHSHIRE else "ward"
            for (ward,), lines in council_lines.group_by("ward", maintain_order=True):
                lines = lines.sort("number")
                first = lines.row(0, named=True)
                ward_key = f"{code}:{slug(ward)}:{DAY.year}"
                ward_area = cs.area("gb.ward", ward_key)
                if ward_area in cs.minted:
                    cs.add("area", id=ward_area, scheme_code="gb.ward")
                    cs.add("area_code", area_id=ward_area, code=ward_key, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=ward_area, parent_id=area, valid_from=None, valid_to=None)
                    name(ward_area, ward)
                unit = cs.unit("gb.ward", ward_key)
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="gb.ward",
                        code=ward_key,
                        area_relation="equals",
                        area_id=ward_area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, ward)
                seats = first["seats"]
                tacit = lines["votes"].is_null().all()
                contest_key = f"{DAY}:local:{ward_key}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=ward_area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems["fptp" if seats == 1 else "block"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{ward} {noun}, {council_name}, {DAY.year}")
                cs.add("slug", path=f"{path}/{DAY}-local/{slug(ward)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=sources[code],
                    is_default=True,
                    completeness=1,
                )
                locator: dict[str, object] = {"ward": ward}
                if first["page"] is not None:
                    locator["page"] = first["page"]
                else:
                    locator["url"] = first["locator"]
                cs.add("citation", entity_id=result_set, source_id=sources[code], locator=locator)
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    description = line["description"]
                    party = parties[PARTIES[description]] if description in PARTIES else None
                    label = line["name"] if line["known_as"] is None else f"{line['name']} “{line['known_as']}”"
                    if line["surname"] is None and line["page"] is not None:
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=f"The declaration prints “commonly known as {line['known_as']}” where the surname "
                            f"belongs, with the forenames {line['name']}; the name is shown as printed.",
                        )
                    if description is not None and party is None and description not in INDEPENDENT:
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=f"{label} stood as “{description}”; no party is linked.",
                        )
                    option = cs.key(f"{SOURCE}.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=label,
                    )
                    person = cs.key(f"{SOURCE}.person", option_key)
                    cs.add("person", id=person)
                    name(person, label)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        member_of_party_id=party,
                        is_independent=description in INDEPENDENT,
                    )
                    if not tacit:
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
                    if line["elected"] or tacit:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if not tacit:
                    floor = lines.filter(pl.col("elected")).sort("votes").row(0, named=True)
                    above = lines.filter(~pl.col("elected") & (pl.col("votes") > floor["votes"]))
                    for line in above.iter_rows(named=True):
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=f"The council's page marks {floor['name']} elected with {floor['votes']:,} votes and "
                            f"{line['name']} not elected with {line['votes']:,}; both are shown as published.",
                        )
                if tacit:
                    continue
                for measure, field in ((REGISTERED, "registered"), (BALLOTS, "ballots"), (INVALID, "rejected")):
                    if first[field] is None:
                        continue
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
