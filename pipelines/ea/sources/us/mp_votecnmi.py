import html
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.mp_votecnmi"
VERSION = "3"
PUBLISHER = "Commonwealth Election Commission of the Northern Mariana Islands"
YEARS = {
    2014: date(2014, 11, 4),
    2016: date(2016, 11, 8),
    2018: date(2018, 11, 6),
    2020: date(2020, 11, 3),
    2022: date(2022, 11, 8),
    2024: date(2024, 11, 5),
}
MUNICIPIO = {"ED6": "69120", "ED7": "69100", "ED4D": "69085"}
HEADINGS = (
    "DELEGATE TO",
    "GOVERNOR",
    "SENAT",
    "HOUSE OF",
    "MAYOR",
    "ATTORNEY",
    "BOARD OF",
    "COUNCIL",
    "JUSTICE",
    "INITIATIVE",
    "ABSENTEE",
)


def url(year: int) -> str:
    return f"https://www.votecnmi.gov.mp/election-results-archive/{year}-election-results"


def fetch() -> None:
    for year in YEARS:
        try:
            raw.cached(SOURCE, url(year))
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfkL", "--compressed", "-A", raw.USER_AGENT, url(year)], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url(year), body, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")


SECTIONS = {
    "DELEGATE": None,
    "GOVERNOR": None,
    "ABSENTEE VOTING": None,
    "SENAT": "senate",
    "HOUSE OF": "house",
    "MAYOR": "mayor",
    "ATTORNEY": "attorney-general",
    "COUNCIL": "municipal-council",
    "BOARD OF EDUCATION": "board-of-education",
}
HOUSE = {1: 6, 2: 2, 3: 6, 4: 2, 5: 2, 6: 1, 7: 1}
PLACES = {"saipan": "ED[1-5]", "northern-islands": "ED4D", "tinian-and-aguiguan": "ED6", "rota": "ED7"}
SENATE = {"1": ("1st", "rota"), "2": ("2nd", "tinian-and-aguiguan"), "3": ("3rd", "saipan")}


def flat(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def heads(header: list[str]) -> list[str]:
    names = [re.sub(r"\s*\(NI\)$", "", text.upper()).replace("ED4D2", "ED4D") for text in header]
    names = [re.sub(r"\s+", "-", name) for name in names]
    for district in "1234":
        split = [name for name in names if re.fullmatch(f"ED{district}[A-D]", name)]
        if len(split) == 1 and district != "4":
            names = [f"ED{district}" if name == split[0] else name for name in names]
        elif split and f"ED{district}" in names:
            names = [f"ED{district}A" if name == f"ED{district}" else name for name in names]
    return names


def local_tables(page: str) -> list[tuple[str, str | None, bool, list[str], list[tuple[str, list[str], bool]]]]:
    found_tables = []
    section = None
    previous = 0
    for found in re.finditer(r"<tr[^>]*>(?:(?!</tr>).)*?CANDIDATE/AFFIL", page, flags=re.DOTALL):
        between = flat(page[max(previous, found.start() - 3000) : found.start()]).upper()
        hits = [(between.rfind(word), word) for word in SECTIONS if word in between]
        if hits:
            section = SECTIONS[max(hits)[1]]
        grid = []
        for row in re.finditer(r"<tr.*?</tr>", page[found.start() :], flags=re.DOTALL):
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row[0], flags=re.DOTALL)
            if not cells:
                continue
            texts = [flat(cell) for cell in cells]
            grid.append((texts[0], texts[1:], "#FF0000" in row[0].upper()))
            previous = found.start() + row.end()
            if texts[0].upper() == "TOTAL":
                break
        if section is None:
            continue
        if section == "senate":
            number = re.findall(r"([123]) ?(?:ST|ND|RD) SENATORIAL", between)[-1]
            place = f"{SENATE[number][0]}-senatorial-district"
        elif section == "house":
            place = "district-" + re.findall(r"DISTRICT (\d)", between)[-1]
        elif section == "attorney-general":
            place = None
        else:
            tail = between[-80:]
            place = (
                "saipan"
                if "SAIPAN" in tail
                else "northern-islands"
                if "NORTHERN ISLANDS" in tail
                else "tinian-and-aguiguan"
                if "TINIAN" in tail
                else "rota"
            )
        found_tables.append((section, place, "SPECIAL" in between, heads(grid[0][1]), grid[1:-1]))
    return found_tables


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Mariana Islands governor, delegate, Senate, House, mayor, attorney general, municipal council, and Board of Education elections 2014-2024 by election district, with early and absentee voting",
            polity="US-MP",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-MP'").fetchone()
        parent, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        ).fetchone()
        municipios = {
            code: (unit, unit_area)
            for code, unit, unit_area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and polity_id = %s",
                [state],
            ).fetchall()
        }
        office = cs.key("us.office", "governor:MP")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=state, kind="governor")
            cs.add(
                "name",
                entity_id=office,
                text="Governor of the Northern Mariana Islands",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        two_round = cs.key("us.electoral_system", "two_round")
        persons = {}
        made = set()
        parties = {
            code: entity
            for code, value in (("DEM", "democratic-party"), ("REP", "republican-party"))
            for (entity,) in conn.execute(
                "select entity_id from external_id where scheme = 'us.party' and value = %s", [value]
            ).fetchall()
        }
        for year, day in YEARS.items():
            title = f"{year} Election Results"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url(year),
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url(year)), source)
            page = raw.cached(SOURCE, url(year)).path.read_text(encoding="utf-8", errors="replace")
            tables = {}
            units = {}
            for found in re.finditer(r"<tr[^>]*>(?:(?!</tr>).)*?CANDIDATE/AFFIL", page, flags=re.DOTALL):
                before = re.sub(
                    r"\s+",
                    " ",
                    html.unescape(re.sub(r"<[^>]+>", " ", page[max(0, found.start() - 1500) : found.start()])),
                ).upper()
                kind = max((before.rfind(heading), heading) for heading in HEADINGS)[1]
                if kind in ("DELEGATE TO", "GOVERNOR") and kind not in tables:
                    tables[kind] = found.start()
            for kind, position in tables.items():
                grid = []
                for row in re.findall(r"<tr.*?</tr>", page[position:], flags=re.DOTALL):
                    cells = [
                        html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                        for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
                    ]
                    if cells:
                        grid.append(cells)
                    if cells and cells[0].upper() == "TOTAL":
                        break
                header, *body = grid
                body = body[:-1]
                columns = [
                    re.match(r"ED\d[A-D]?", text.upper())[0]
                    if text.upper().startswith("ED")
                    else re.sub(r"\s+", "-", text.upper())
                    for text in header[1:]
                ]
                governor = kind == "GOVERNOR"
                contest_key = f"{day}:MP:governor" if governor else f"{day}:MP:house:0"
                election = conn.execute(
                    "select c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                    [f"{day}:MP:house:0"],
                ).fetchone()[0]
                if governor:
                    contest = cs.key("us.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="office",
                        office_id=office,
                        district_area_id=area,
                        term="full",
                        magnitude=1,
                        round_number=1,
                        electoral_system_id=two_round,
                        default_result_kind="counted",
                    )
                    cs.add(
                        "name",
                        entity_id=contest,
                        text=f"{year} Northern Mariana Islands governor election",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
                    cs.add("slug", path=f"us/mp/{day}-general/governor", entity_id=contest)
                    options = []
                    for row in body:
                        ticket = re.sub(r"/[A-Za-z. ]+$", "", row[0]).replace("*", "")
                        names = [part.strip() for part in re.split(r"\s+&\s+|/", ticket) if part.strip()]
                        people = [
                            " ".join(reversed([piece.strip() for piece in name.split(",", 1)])).title()
                            for name in names
                        ]
                        token = slug(" ".join(people))
                        option = cs.key("us.option", f"{contest_key}:{token}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            ballot_label=" and ".join(people),
                        )
                        for role, label in zip(("head", "running_mate"), people, strict=False):
                            person = persons.setdefault(slug(label), cs.key(f"{SOURCE}.person", f"MP:{slug(label)}"))
                            if person in cs.minted and not any(r["id"] == person for r in cs.rows.get("person", [])):
                                cs.add("person", id=person)
                                cs.add(
                                    "name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True
                                )
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{contest_key}:{token}:{role}"),
                                person_id=person,
                                option_id=option,
                                role=role,
                                ballot_name=label if role == "head" else None,
                                is_independent=False,
                            )
                        options.append(option)
                else:
                    contest = conn.execute(
                        "select c.id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                        [contest_key],
                    ).fetchone()[0]
                    labels = conn.execute(
                        "select id, ballot_label from contest_option where contest_id = %s", [contest]
                    ).fetchall()
                    options = []
                    for row in body:
                        surname, given = (
                            part.strip().lower() for part in row[0].split("/")[0].replace("*", "").split(",", 1)
                        )
                        found = [
                            option
                            for option, label in labels
                            if surname in label.lower() and given.split()[0] in label.lower()
                        ]
                        if len(found) != 1:
                            raise ValueError(f"{year}: {len(found)} options for {row[0]}")
                        options.append(found[0])
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:district")
                counted = sum(int(row[1 + columns.index("TOTAL")].replace(",", "")) for row in body)
                if governor:
                    completeness = 1
                else:
                    columns_set = (
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
                    )
                    for prior in conn.execute(
                        f"select {', '.join(columns_set)} from result_set where contest_id = %s and is_default and id <> %s",
                        [contest, result_set],
                    ).fetchall():
                        cs.add("result_set", **dict(zip(columns_set, prior, strict=True)), is_default=False)
                    for option, outcome_kind, seats in conn.execute(
                        """select o.option_id, o.kind, o.seats from outcome o
                           join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
                        [contest, result_set],
                    ).fetchall():
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome_kind, seats=seats)
                    official = conn.execute(
                        """select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k
                           where r.contest_id = %s and r.is_default and v.measure = 1""",
                        [contest],
                    ).fetchone()[0]
                    completeness = round(float(counted) / float(official), 4)
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
                    completeness=completeness,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md="Votes by election district as published; early voting and absentee votes are separate aggregate units on their own channels where the page gives them separate columns.",
                )
                if governor:
                    ranked = sorted(
                        zip(
                            options,
                            (int(row[1 + columns.index("TOTAL")].replace(",", "")) for row in body),
                            strict=True,
                        ),
                        key=lambda pair: -pair[1],
                    )
                    cs.add("outcome", result_set_id=result_set, option_id=ranked[0][0], kind="elected", seats=None)
                has_early = any(name.startswith("EV") for name in columns)
                for row, option in zip(body, options, strict=True):
                    for name, cell in zip(columns, row[1:], strict=False):
                        if name == "TOTAL" or not cell.replace(",", "").isdigit():
                            continue
                        aggregate = name.startswith(("EV", "ABS"))
                        code = f"MP:{year}:{name}" if aggregate else f"{year}:MP:{name}"
                        scheme = "us.aggregate" if aggregate else "us.precinct"
                        channel = (2 if name.startswith("EV") else 3) if aggregate else (1 if has_early else 0)
                        if code not in units:
                            units[code] = cs.unit(scheme, code)
                            municipio = None if aggregate else municipios[MUNICIPIO.get(name, "69110")]
                            cs.add(
                                "reporting_unit",
                                id=units[code],
                                polity_id=state,
                                scheme_code=scheme,
                                code=code,
                                parent_id=municipio[0] if municipio else parent,
                                area_relation="within",
                                area_id=municipio[1] if municipio else area,
                                kind="aggregate" if aggregate else "standard",
                                valid_from=day,
                                valid_to=day + timedelta(days=1),
                            )
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=units[code],
                            option_id=option,
                            channel=channel,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=int(cell.replace(",", "")),
                        )
            election = conn.execute(
                "select c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [f"{day}:MP:house:0"],
            ).fetchone()[0]
            for section, place, special, columns, body in local_tables(page):
                tail = f"{section}:{place}" if place else section
                contest_key = f"{day}:MP:69:{tail}" + (":special" if special else "")
                office_name = f"Northern Mariana Islands {section.replace('-', ' ').title().replace(' Of ', ' of ')}"
                seat_name = f"{office_name}, {(place or '').replace('-', ' ').title().replace(' And ', ' and ')}"
                if section == "house" and place:
                    width = f"ED{place[-1]}"
                elif section == "senate" and place:
                    width = PLACES[SENATE[place[0]][1]]
                else:
                    width = PLACES.get(place or "", "ED")
                winners = sum(red for _, _, red in body)
                magnitude = (
                    HOUSE[int(place[-1])]
                    if section == "house" and place
                    else 3
                    if section == "municipal-council"
                    else winners
                )
                if winners == 0 or winners > magnitude or (section == "house" and winners != magnitude):
                    raise ValueError(f"{contest_key}: {winners} winners marked for {magnitude} seats")
                is_body = section not in ("mayor", "attorney-general")
                if is_body:
                    holder = cs.key("us.body", f"MP:69:{section}")
                else:
                    holder = cs.key("us.office", f"MP:69:{section}")
                if holder in cs.minted:
                    raise ValueError(f"{contest_key}: no held body or office")
                district = area
                if place:
                    district = cs.area("us.local_district", f"MP:69:{tail}")
                    if district in cs.minted and district not in made:
                        made.add(district)
                        cs.add("area", id=district, scheme_code="us.local_district")
                        cs.add("area_code", area_id=district, code=f"MP:69:{tail}", valid_from=None)
                        cs.add("area_parent", area_id=district, parent_id=area, valid_from=None)
                        cs.add(
                            "name",
                            entity_id=district,
                            text=seat_name,
                            lang="en",
                            kind="official",
                            is_preferred=True,
                        )
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if is_body else "office",
                    office_id=None if is_body else holder,
                    body_id=holder if is_body else None,
                    district_area_id=district,
                    term="remainder" if special else "full",
                    magnitude=magnitude,
                    round_number=1,
                    electoral_system_id=cs.key("us.electoral_system", "fptp" if magnitude == 1 else "block"),
                    default_result_kind="counted",
                )
                title = conn.execute(
                    """select n.text from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                       and x.value = %s join name n on n.entity_id = c.id and n.is_preferred""",
                    [f"2026-11-03:MP:69:{tail}"],
                ).fetchone()
                label = (
                    title[0].removeprefix("2026 ").removesuffix(" election")
                    if title
                    else seat_name
                    if place
                    else office_name
                )
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{year} {label} {'special election' if special else 'election'}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add(
                    "slug",
                    path=f"us/mp/{day}-general/{tail.replace(':', '-')}" + ("-special" if special else ""),
                    entity_id=contest,
                )
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:district")
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
                    body_md="Votes by election district as published; early voting and absentee votes are separate aggregate units on their own channels. Winners are the totals the page marks in red as official winners.",
                )
                has_early = any(name.startswith("EV") for name in columns)
                for text, cells, red in body:
                    if len(cells) != len(columns):
                        raise ValueError(f"{contest_key}: {text} has {len(cells)} cells for {len(columns)} columns")
                    values = {
                        name: int(cell.replace(",", ""))
                        for name, cell in zip(columns, cells, strict=True)
                        if name != "%" and cell.replace(",", "").isdigit()
                    }
                    total = values.pop("TOTAL")
                    if sum(values.values()) != total:
                        raise ValueError(f"{contest_key}: {text} units add to {sum(values.values())}, not {total}")
                    stray = [
                        name
                        for name, value in values.items()
                        if value and name.startswith("ED") and not re.match(width, name)
                    ]
                    if stray:
                        raise ValueError(f"{contest_key}: {text} has votes in {stray}")
                    printed = re.sub(r"\*", "", text)
                    party_code = None
                    if "/" in printed:
                        printed, party_code = (part.strip() for part in printed.rsplit("/", 1))
                    surname, given = (
                        (part.strip() for part in printed.split(",", 1))
                        if "," in printed
                        else re.match(r"([A-Z][A-Z' -]+?)\s+([A-Z][a-z].*)", printed).groups()
                    )
                    suffix = re.search(r"\b(Jr|Sr)\.?(?=\s|$)", given)
                    person_name = f"{re.sub(r'\s+', ' ', given.replace(suffix[0], '')).strip() if suffix else given} {surname.title()}"
                    if suffix:
                        person_name += f" {suffix[1]}."
                    party = parties.get((party_code or "").rstrip(".").upper())
                    token = slug(person_name)
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=person_name,
                    )
                    person = persons.setdefault(token, cs.key(f"{SOURCE}.person", f"MP:{token}"))
                    if person in cs.minted and not any(r["id"] == person for r in cs.rows.get("person", [])):
                        cs.add("person", id=person)
                        cs.add(
                            "name", entity_id=person, text=person_name, lang="en", kind="official", is_preferred=True
                        )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=person_name,
                        member_of_party_id=party,
                        is_independent=party is None,
                        is_incumbent="*" in text,
                    )
                    if red:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="elected",
                            seats=1 if is_body else None,
                        )
                    for name, value in values.items():
                        aggregate = name.startswith(("EV", "ABS"))
                        code = f"MP:{year}:{name}" if aggregate else f"{year}:MP:{name}"
                        scheme = "us.aggregate" if aggregate else "us.precinct"
                        if code not in units:
                            units[code] = cs.unit(scheme, code)
                            municipio = None if aggregate else municipios[MUNICIPIO.get(name, "69110")]
                            cs.add(
                                "reporting_unit",
                                id=units[code],
                                polity_id=state,
                                scheme_code=scheme,
                                code=code,
                                parent_id=municipio[0] if municipio else parent,
                                area_relation="within",
                                area_id=municipio[1] if municipio else area,
                                kind="aggregate" if aggregate else "standard",
                                valid_from=day,
                                valid_to=day + timedelta(days=1),
                            )
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=units[code],
                            option_id=option,
                            channel=(2 if name.startswith("EV") else 3) if aggregate else (1 if has_early else 0),
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
