import re
import subprocess
from collections import Counter
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.elector_districts"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
CERTIFICATE = "https://www.archives.gov/files/electoral-college/{year}/{prefix}ascertainment-{state}.pdf"
YEARS = {2012: "2012-", 2016: "", 2020: "", 2024: ""}
STATES = {"ME": "maine", "NE": "nebraska"}
LLDC = "https://lldc.mainelegislature.org/Open/Elections/Returns/{year}/{day}/elec_{day}{file}"
TOWNS = {
    1988: ("19881108", "i.pdf", ["h.pdf"]),
    1992: ("19921103", "d.xlsx", []),
    1996: ("19961105", "f.xlsx", ["l.xlsx"]),
    2000: ("20001107", "b.xlsx", ["m.xlsx"]),
    2004: ("20041102", "m.xlsx", ["w.xlsx"]),
    2008: ("20081104", "p.xlsx", ["z.xlsx", "ab.xlsx"]),
}
CANVASS = {2008: "https://sos.nebraska.gov/sites/default/files/doc/elections/2008/2008%20General%20Canvass%20Book.pdf"}
PAIRS = (
    [(year, state) for year in YEARS for state in STATES]
    + [(year, "ME") for year in TOWNS]
    + [(year, "NE") for year in CANVASS]
)
DAYS = {
    1988: "1988-11-08",
    1992: "1992-11-03",
    1996: "1996-11-05",
    2000: "2000-11-07",
    2004: "2004-11-02",
    2008: "2008-11-04",
    2012: "2012-11-06",
    2016: "2016-11-08",
    2020: "2020-11-03",
    2024: "2024-11-05",
}
METHOD = {
    "certificate": (
        "Votes by congressional district for each ticket's district elector, from the state's certificate of "
        "ascertainment as the National Archives publishes it. The certificate lists only the tickets on the ballot, "
        "so write-ins, blanks, and scattering are not here; completeness is the tickets' share of the statewide vote."
    ),
    "towns": (
        "Votes by congressional district, summed from the Secretary of State's official vote by town, ward, and "
        "precinct as the Maine State Law and Legislative Reference Library publishes it. Each town is assigned to its "
        "district by the same year's returns for Representative to Congress; the districts sum to the statewide "
        "official vote for every candidate."
    ),
    "canvass": (
        "Votes by congressional district for each ticket, from the Secretary of State's official canvass book, "
        "which reports the presidential vote by district. The district tables leave out write-ins, so completeness is "
        "the tickets' share of the statewide vote."
    ),
}
MAINE = (
    "Maine counted {uocava} votes from uniformed-service and overseas voters only toward its at-large electors, not "
    "toward either district, so the two districts sum to slightly less than the statewide vote."
)
WALDO = (
    "Waldo County was split between the districts in 1988. Its towns' votes come from the vote book's town table, "
    "read by optical character recognition and checked against the county total; every other county lies wholly in "
    "one district."
)


def documents(year: int, state: str) -> list[str]:
    if year in YEARS:
        return [CERTIFICATE.format(year=year, prefix=YEARS[year], state=STATES[state])]
    if state == "NE":
        return [CANVASS[year]]
    day, president, house = TOWNS[year]
    return [LLDC.format(year=year, day=day, file=name) for name in (president, *house)]


def fetch() -> None:
    for year, state in PAIRS:
        for address in documents(year, state):
            raw.fetch(SOURCE, address, suffix=Path(address).suffix)


def text(address: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, address).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def place(county: str, town: str, ward: str | None) -> tuple[str, str, str]:
    return (
        county.strip().upper(),
        re.sub(r"\s+", " ", town.strip().upper()),
        re.sub(r"[^0-9WP]", "", (ward or "").upper()),
    )


def parse() -> None:
    rows = []
    for year, state in PAIRS:
        addresses = documents(year, state)
        uocava = short = 0
        if year in YEARS and state == "NE":
            for block in re.split(r"\n(?=\S[^\n]*,\s*Pre[^\n]*/)", text(addresses[0]))[1:]:
                numbers = [
                    int(re.sub(r"\D", "", found)) for found in re.findall(r"Vote\w*\s+Rec[^:\n]*:\s*(\d[\d ,]*)", block)
                ][:4]
                head = re.search(r"\s{2,}(.*?),\s*Pre", block.split("\n", 1)[0])
                if len(numbers) != 4 or numbers[0] != sum(numbers[1:]) or head is None:
                    raise ValueError(f"{year} {state}: unreadable ticket {block[:80]!r} {numbers}")
                rows.append((year, state, head.group(1), *numbers))
        elif year in YEARS:
            certificate = text(addresses[0])
            found = re.search(r"There were ([\d,]+) votes", certificate)
            uocava = int(found.group(1).replace(",", "")) if found else 0
            for block in re.split(r"(?=The [A-Z ]+(?:PARTY|ALL)\s+Electors)", certificate)[1:]:
                numbers = [
                    int(re.sub(r"\D", "", found.group(1)))
                    for line in block.split("\n")
                    if re.search(r"[a-z]", line)
                    and not re.search(r"District|Large|Congress|votes|There were|President", line, re.IGNORECASE)
                    and (found := re.search(r"\s(\d[\d ,]*\d|\d)\s*$", line))
                ][:3]
                head = re.search(r"candidacy\s*of\s*(.*?)\s*(?:/or|for)\s+President", block, re.DOTALL)
                if len(numbers) != 3 or head is None or not 0 <= numbers[2] - numbers[0] - numbers[1] <= uocava:
                    raise ValueError(f"{year} {state}: unreadable ticket {block[:80]!r} {numbers}")
                rows.append((year, state, re.sub(r"\s+", " ", head.group(1)), numbers[2], *numbers[:2], None))
                short += numbers[2] - numbers[0] - numbers[1]
            if short > uocava:
                raise ValueError(f"{year} {state}: the districts fall short by more than the {uocava} overseas votes")
        elif state == "NE":
            book = text(addresses[0])
            heading = re.search(r"^For President of the United States\s*$", book, re.MULTILINE)
            total = re.search(r"^TOTAL +([\d ]+)$", book[heading.end() :] if heading else "", re.MULTILINE)
            if total is None:
                raise ValueError(f"{year} {state}: no statewide table")
            statewide = [int(number) for number in total.group(1).split()]
            districts = []
            for block in re.split(
                r"Congressional District (?:One|Two|Three)\n",
                book[book.index("For President of the United States by Congressional District") :],
            )[1:4]:
                found = re.search(r"^TOTAL +([\d ]+)$", block, re.MULTILINE)
                names = block.split("\n")[1].split()
                if found is None or len(names) != len(found.group(1).split()):
                    raise ValueError(f"{year} {state}: unreadable district table {block[:80]!r}")
                districts.append(dict(zip(names, map(int, found.group(1).split()), strict=True)))
            for number, name in enumerate(districts[0]):
                values = [district[name] for district in districts]
                if statewide[number] != sum(values):
                    raise ValueError(
                        f"{year} {state}: {name}'s districts sum to {sum(values)}, not {statewide[number]}"
                    )
                rows.append((year, state, name, statewide[number], *values))
        elif year == 1988:
            book, house = text(addresses[0]), text(addresses[1])
            names = ["Bush", "Dukakis", "Fulani", "Paul", "Others"]
            summary = book[book.index("STATE SUMMARY OF RESULTS") :]
            counties = {
                found.group(1): [int(number) for number in found.group(2).split()]
                for found in re.finditer(
                    r"^\s*([A-Z][a-z]+) +((?:\d+ +){4}\d+) *$", summary[: summary.index("STATE TOTALS")], re.MULTILINE
                )
            }
            totals = re.search(r"STATE TOTALS\s+(.*)", summary)
            statewide = [int(number.replace(",", "")) for number in totals.group(1).split()] if totals else []
            if len(counties) != 16 or [sum(column) for column in zip(*counties.values(), strict=True)] != statewide:
                raise ValueError(f"{year} {state}: the county summary does not sum to the state totals")
            first = house[house.index("DISTRICT 1 SUMMARY") : house.index("DISTRICT 2 SUMMARY")]
            second = house[house.index("DISTRICT 2 SUMMARY") :]
            ones = set(re.findall(r"^\s*([A-Z][a-z]+)\s+\d", first[: first.index("STATE")], re.MULTILINE))
            twos = set(re.findall(r"^\s*([A-Z][a-z]+)\s+\d", second[: second.index("STATE")], re.MULTILINE))
            if ones | twos != set(counties) or ones & twos != {"Waldo"}:
                raise ValueError(f"{year} {state}: the districts do not partition the counties apart from Waldo")
            section = house[house.index("COUNTY: WALDO") :]
            section = section[: section.index("TOTALS:")]
            west = {found.group(1) for found in re.finditer(r"^([A-Z][A-Za-z.' ]*?)\s{2,}\d", section, re.MULTILINE)}
            section = book[book.index("COUNTY: WALDO") :]
            lines = section[: section.index("\n", section.index("TOTALS:"))].split("\n")
            ends = [number.end() for number in re.finditer(r"\d[\d,]*", lines[-1])]
            towns = {}
            for line in lines[:-1]:
                found = re.match(r"([A-Z][A-Za-z.' ]*?)\s{2,}(?=\d)", line)
                if found is None:
                    continue
                votes = [0] * 5
                for figure in re.compile(r"\d[\d,]*").finditer(line, found.end()):
                    column = min(range(5), key=lambda index: abs(ends[index] - figure.end()))
                    votes[column] += int(figure.group().replace(",", ""))
                towns[found.group(1)] = votes
            if (
                len(ends) != 5
                or not west <= set(towns)
                or [sum(column) for column in zip(*towns.values(), strict=True)] != counties["Waldo"]
            ):
                raise ValueError(f"{year} {state}: Waldo's towns do not sum to its county total")
            for number, name in enumerate(names):
                one = sum(counties[county][number] for county in ones - {"Waldo"}) + sum(
                    towns[town][number] for town in west
                )
                two = sum(counties[county][number] for county in twos - {"Waldo"}) + sum(
                    votes[number] for town, votes in towns.items() if town not in west
                )
                if one + two != statewide[number]:
                    raise ValueError(f"{year} {state}: {name}'s districts do not sum to the state total")
                rows.append((year, state, name, statewide[number], one, two, None))
        else:
            sheets = [
                fastexcel.read_excel(raw.cached(SOURCE, address).path)
                .load_sheet(0, header_row=None, dtypes="string")
                .to_polars()
                .rows()
                for address in addresses
            ]
            district = {}
            for sheet in sheets[1:]:
                for row in sheet:
                    if row[0] == "CG":
                        district[place(row[2], row[3], row[4])] = int(float(row[1]))
                    elif year == 2000 and row[0] and row[0].strip().isdigit() and row[4]:
                        district[place(row[1], row[2], row[3])] = int(float(row[4]))
            totals: Counter[tuple[int, str]] = Counter()
            for row in sheets[0]:
                if year == 1992 and row[0] and row[0].strip().isdigit():
                    unit = int(float(row[3]))
                    pairs = [*zip(row[11:17], row[4:10], strict=True), ("Others", row[10])]
                elif year == 2000 and row[0] and row[0].strip().isdigit():
                    unit = district[place(row[1], row[2], row[3])]
                    pairs = [*((row[index + 1], row[index]) for index in range(4, 16, 2)), ("Others", row[16])]
                elif year in (1996, 2004) and row[0] and len(row[0].strip()) == 3 and row[3] is not None:
                    unit = district[place(row[0], row[1], row[2])]
                    pairs = [(row[index], row[index + 1]) for index in range(3, len(row) - 1, 2) if row[index]]
                elif year == 2008 and row[0] == "PR":
                    unit = district[place(row[1], row[2], row[3])]
                    pairs = [*((row[index], row[index + 2]) for index in range(4, 16, 3)), ("Others", row[17])]
                else:
                    continue
                for name, votes in pairs:
                    if name is not None and votes is not None:
                        surname = name.split(",")[0].strip().title()
                        totals[(unit, "Others" if surname in ("Other", "Others") else surname)] += int(float(votes))
            for name in dict.fromkeys(name for _, name in totals):
                rows.append(
                    (
                        year,
                        state,
                        name,
                        totals[(1, name)] + totals[(2, name)],
                        totals[(1, name)],
                        totals[(2, name)],
                        None,
                    )
                )
        rows.append((year, state, "", uocava, None, None, None))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int16,
            "state": pl.String,
            "candidate": pl.String,
            "statewide": pl.Int64,
            "d1": pl.Int64,
            "d2": pl.Int64,
            "d3": pl.Int64,
        },
        orient="row",
    ).write_parquet(PARSED / "certificates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "certificates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's presidential votes by congressional district 1988–2024 and Nebraska's 2008–2024, from the states' certificates of ascertainment, Maine's official vote by town, and Nebraska's canvass book",
            polity="US",
        )
        for (year, state), lines in frame.group_by("year", "state", maintain_order=True):
            day = DAYS[int(year)]
            if cs.key(f"{SOURCE}.result_set", f"{day}:{state}-01:president") not in cs.minted:
                continue
            uocava = int(lines.filter(pl.col("candidate") == "")["statewide"][0])
            lines = lines.filter(pl.col("candidate") != "")
            kind = "certificate" if year in YEARS else "towns" if state == "ME" else "canvass"
            addresses = documents(int(year), str(state))
            publisher, title, license = {
                "certificate": (
                    "National Archives and Records Administration",
                    f"{year} certificate of ascertainment, {'Maine' if state == 'ME' else 'Nebraska'}",
                    "Public domain",
                ),
                "towns": (
                    "Maine Department of the Secretary of State",
                    f"{year} general election, official vote for President by town",
                    "Public record",
                ),
                "canvass": ("Nebraska Secretary of State", f"{year} general canvass book", "Public record"),
            }[kind]
            source = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=addresses[0],
                license=license,
            )
            for address in addresses:
                cs.artifact(raw.cached(SOURCE, address), source)
            options = conn.execute(
                """select o.id, o.kind, o.party_id, v.value::bigint,
                          (select array_agg(array[cy.role::text, cy.person_id::text, n.text] order by cy.role)
                           from candidacy cy join name n on n.entity_id = cy.person_id and n.is_preferred
                           where cy.option_id = o.id), o.ballot_label
                   from external_id x join contest_option o on o.contest_id = x.entity_id and o.kind in ('ticket', 'write_in')
                   join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                   where x.scheme = 'us.contest' and x.value = %s""",
                [f"{day}:{state}:president"],
            ).fetchall()
            total = conn.execute(
                """select sum(v.value)::bigint from external_id x
                   join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 1 and v.option_k <> 0
                   where x.scheme = 'us.contest' and x.value = %s""",
                [f"{day}:{state}:president"],
            ).fetchone()
            if total is None or total[0] is None:
                raise ValueError(f"{day}:{state}:president has no default votes")
            tickets = []
            for candidate, statewide, *districts in lines.select(
                "candidate", "statewide", "d1", "d2", "d3"
            ).iter_rows():
                values = [value for value in districts if value is not None]
                surname = slug(candidate).split("-")[-1]
                found = [row for row in options if row[3] == statewide and row[4]] or [
                    row
                    for row in options
                    if any(role == "head" and slug(text).split("-")[-1] == surname for role, _, text in row[4] or [])
                ]
                scattered = [row for row in options if row[1] == "write_in" and not row[4] and row[3] == statewide]
                if not found and len(scattered) == 1:
                    tickets.append(("write_in", None, [], values, scattered[0][5]))
                    continue
                if not found and candidate == "Others":
                    if statewide != sum(row[3] for row in options if row[1] == "write_in"):
                        raise ValueError(f"{day}:{state}: others ({statewide}) are not the write-ins on file")
                    tickets.append(("others", None, [], values, "Write-ins"))
                    continue
                if len(found) != 1:
                    raise ValueError(f"{day}:{state}: {candidate} ({statewide}) matches {len(found)} tickets")
                option, option_kind, party, held, candidacies, _ = found[0]
                if abs(held - statewide) > max(5, held * 0.001):
                    raise ValueError(f"{day}:{state}: {candidate} {statewide} against {held} on file")
                tickets.append((option_kind, party, candidacies, values, None))
            counted = int(lines["statewide"].sum())
            for number in range(1, 3 if state == "ME" else 4):
                district_key = f"{day}:{state}-{number:02d}:president"
                contest, election, unit, existing = conn.execute(
                    """select c.id, c.election_id, u.id, rs.id from external_id x join contest c on c.id = x.entity_id
                       join reporting_unit u on u.area_id = c.district_area_id and u.scheme_code = 'us.cd'
                       join result_set rs on rs.contest_id = c.id and rs.is_default
                       where x.scheme = 'us.contest' and x.value = %s""",
                    [district_key],
                ).fetchone() or (None, None, None, None)
                if contest is None:
                    raise ValueError(f"{district_key} is not held")
                declared = conn.execute(
                    """select cy.person_id from outcome o join candidacy cy on cy.option_id = o.option_id
                       and cy.role = 'head' where o.result_set_id = %s and o.kind = 'won'""",
                    [existing],
                ).fetchone()
                for row in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                              supersedes_id from result_set where id = %s""",
                    [existing],
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
                                row,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )
                result_set = cs.key(f"{SOURCE}.result_set", district_key)
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
                    completeness=round(counted / total[0], 4),
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD[kind])
                if uocava:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=MAINE.format(uocava=f"{uocava:,}"))
                if year == 1988 and state == "ME":
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=WALDO)
                ranked = []
                for option_kind, party, candidacies, districts, label in tickets:
                    head = next((person for role, person, _ in candidacies if role == "head"), None)
                    option_key = f"{district_key}:{head or 'others'}"
                    option = cs.key("us.option", option_key)
                    if option in cs.minted:
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind=option_kind,
                            party_id=party,
                            ballot_label=label,
                        )
                        for role, person, text in candidacies:
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{option_key}:{role}"),
                                person_id=person,
                                option_id=option,
                                role=role,
                                ballot_name=text,
                                is_independent=False,
                            )
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        value=districts[number - 1],
                        flags=0,
                    )
                    ranked.append((districts[number - 1], head, option))
                ranked.sort(key=lambda item: item[0], reverse=True)
                if declared is None or str(declared[0]) != ranked[0][1] or ranked[0][0] == ranked[1][0]:
                    raise ValueError(f"{district_key}: the certificate's leader is not the declared winner")
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][2], kind="won", seats=1)
        return cs.write()
