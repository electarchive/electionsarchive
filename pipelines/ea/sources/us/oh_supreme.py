import html
import re
import time
import unicodedata
from datetime import date
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.oh_supreme"
VERSION = "1"
PUBLISHER = "Ohio Secretary of State"
PAGE = "https://www.ohiosos.gov/elections/election-results-and-data/"
OLD = "https://web.archive.org/web/2015id_/http://www.sos.state.oh.us/sos/elections/Research/electResultsMain/"
NEW = "https://web.archive.org/web/{}id_/https://www.ohiosos.gov/globalassets/elections/{}"
NOTE = (
    "County totals from the Ohio Secretary of State's official results for the general election. "
    "The Supreme Court ballot is nonpartisan."
)
CAVEAT = (
    "The county table's {name} column adds to {summed:,}, not the {printed:,} the Secretary of State prints as the "
    "statewide total; the counties are kept as printed."
)
NOTE_STATE = (
    "Statewide totals only, from page 6 of the Secretary of State's official canvass of the November 4, 2008 "
    "general election; county returns for the Supreme Court races are not in the canvass."
)
CANVASS_2008 = NEW.format("2023", "2008/gen/08-1104officialcanvass.pdf")
SUPREME = "Justice of the Supreme Court"
CHIEF = "Chief Justice of the Supreme Court"

HTML = {
    date(2000, 11, 7): [
        ("2000ElectionsResults/OHSupCt0101_11072000.aspx", [("Term Commencing 01/01/2001", 2)]),
        ("2000ElectionsResults/OHSupCt0102_11072000.aspx", [("Term Commencing 01/02/2001", 2)]),
    ],
    date(2002, 11, 5): [
        ("2002Results/supco.aspx", [("Term Commencing 01/01/2003", 2), ("Term Commencing 01/02/2003", 2)]),
    ],
    date(2004, 11, 2): [
        ("2004ElectionsResults/04-1102ChiefJusticeSupCt.aspx", [(None, 2)]),
        ("2004ElectionsResults/07-1102JusticeSupCt010105.aspx", [("Term Commencing 01/01/2005", 2)]),
        ("2004ElectionsResults/04-1102JusticeSupCt010205.aspx", [("Term Commencing 01/02/2005", 1)]),
        ("2004ElectionsResults/04-1102JusticeSupremeCourtUnexpired.aspx", [("Unexpired Term", 2)]),
    ],
    date(2006, 11, 7): [
        ("2006ElectionsResults/06-1107SupremeCourt010107.aspx", [("Term Commencing 01/01/2007", 2)]),
        ("2006ElectionsResults/06-1107SupremeCourt010207.aspx", [("Term Commencing 01/02/2007", 2)]),
    ],
    date(2010, 11, 2): [
        ("2010results/20101102justice1.aspx", [("Term Commencing 01/01/2011", 2)]),
        ("2010results/20101102justice2.aspx", [("Term Commencing 01/02/2011", 1)]),
    ],
}
BOOKS = {
    date(2012, 11, 6): (
        "https://web.archive.org/web/2015id_/http://www.sos.state.oh.us/sos/upload/elections/2012/gen/FinalResults.xlsx",
        "Ohio Supreme Court",
        1,
    ),
    date(2014, 11, 4): (
        "https://web.archive.org/web/2015id_/http://www.sos.state.oh.us/SOS/Upload/elections/2014/general/results.xlsx",
        "Supreme Court",
        1,
    ),
    date(2016, 11, 8): (NEW.format("2", "2016/gen/county.xlsx"), "Judicial", 1),
    date(2018, 11, 6): (NEW.format("2023", "2018/gen/2018-11-06_statewidecounty.xlsx"), "Judicial", 1),
    date(2020, 11, 3): (NEW.format("2022", "2020/gen/statewideresultsbycounty.xlsx"), "Judicial", 1),
}
STATE_2008 = {
    "Term Commencing 01/01/2009": {"Maureen O'Connor": 2966416, "Joseph D. Russo": 1450718},
    "Term Commencing 01/02/2009": {"Peter M. Sikora": 1551751, "Evelyn L. Stratton": 2660072},
}
SKIP = {(date(2016, 11, 8), CHIEF)}
NUMBER = re.compile(r"^[\d,]+$")
TERM = re.compile(r"(Term Commencing|Unexpired Term Ending)\s+(\d\d/\d\d/\d{4})")


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def urls() -> dict[date, list[str]]:
    found = {day: [OLD + path for path, _ in pages] for day, pages in HTML.items()}
    for day, (url, _, _) in BOOKS.items():
        found[day] = [url]
    found[date(2008, 11, 4)] = [CANVASS_2008]
    return found


def fetch() -> None:
    for links in urls().values():
        for url in links:
            raw.fetch(
                SOURCE, url, suffix=".pdf" if url.endswith(".pdf") else ".xlsx" if url.endswith(".xlsx") else ".html"
            )
            time.sleep(1)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text).replace("`", "'").replace("*", "")).strip()


def web_table(
    url: str, shape: list[tuple[str | None, int]]
) -> list[tuple[str | None, dict[str, dict[str, int]], dict[str, int]]]:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
    names: list[str] = []
    counties: dict[str, list[int]] = {}
    totals: list[int] = []
    for row in re.findall(r"<tr.*?</tr>", text, re.DOTALL):
        cells = [clean(re.sub(r"<[^>]+>", "", cell)) for cell in re.findall(r"<t[dh].*?</t[dh]>", row, re.DOTALL)]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        if (
            not names
            and cells[0].upper().endswith("COUNTY")
            and len(cells) > 1
            and not any(NUMBER.match(c) for c in cells[1:])
        ):
            names = cells[1:]
        elif names and len(cells) > 1 and all(NUMBER.match(cell) for cell in cells[1:]):
            values = [int(cell.replace(",", "")) for cell in cells[1:]]
            if cells[0].upper() == "TOTAL":
                totals = values
                break
            counties[cells[0]] = values
    want = sum(count for _, count in shape)
    if len(names) > len(totals):
        names[0:2] = [f"{names[0]} {names[1]}"]
    names = names[-want:]
    totals, counties = totals[-want:], {county: values[-want:] for county, values in counties.items()}
    found = []
    start = 0
    for label, count in shape:
        found.append(
            (
                label,
                {
                    county: {names[start + k]: values[start + k] for k in range(count)}
                    for county, values in counties.items()
                },
                {names[start + k]: totals[start + k] for k in range(count)},
            )
        )
        start += count
    return found


def book_table(
    day: date, url: str, sheet: str, name_row: int
) -> list[tuple[str, str | None, dict[str, dict[str, int]]]]:
    rows = pl.read_excel(raw.cached(SOURCE, url).path, sheet_name=sheet, has_header=False, infer_schema_length=0).rows()
    titles = {i: clean(str(v)) for i, v in enumerate(rows[0]) if v is not None and i > 4}
    names = {i: clean(str(v)) for i, v in enumerate(rows[name_row]) if v is not None and i > 4}
    first = next(i for i, row in enumerate(rows) if str(row[0]).strip().lower() == "total")
    total = [int(str(v)) if str(v).isdigit() else 0 for v in rows[first]]
    body = [
        row
        for row in rows[first + 1 :]
        if row[0] is not None and row[1] is not None and not str(row[0]).lower().startswith(("percent", "total"))
    ]
    starts = sorted(titles)
    found = []
    for position, start in enumerate(starts):
        title = titles[start]
        if "Supreme Court" not in title or "Appeals" in title:
            continue
        end = starts[position + 1] if position + 1 < len(starts) else max(names) + 1
        columns = [i for i in range(start, end) if i in names]
        match = TERM.search(title)
        label = f"{match.group(1)} {match.group(2)}" if match else None
        office = CHIEF if "Chief Justice" in title else SUPREME
        places = {str(row[0]).strip(): {names[i]: int(str(row[i])) for i in columns} for row in body}
        for i in columns:
            assert sum(int(str(row[i])) for row in body) == total[i], (day, names[i])
        found.append((office, label, places))
    return found


def contests() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for day, pages in HTML.items():
        for path, shape in pages:
            for label, places, printed in web_table(OLD + path, shape):
                office = CHIEF if label is None else SUPREME
                found.append(
                    {
                        "day": day,
                        "office": office,
                        "label": label,
                        "places": places,
                        "url": OLD + path,
                        "printed": printed,
                    }
                )
    for day, (url, sheet, name_row) in BOOKS.items():
        for office, label, places in book_table(day, url, sheet, name_row):
            if (day, office) not in SKIP:
                found.append({"day": day, "office": office, "label": label, "places": places, "url": url})
    for label, votes in STATE_2008.items():
        found.append(
            {
                "day": date(2008, 11, 4),
                "office": SUPREME,
                "label": label,
                "places": {"Ohio": votes},
                "url": CANVASS_2008,
            }
        )
    return sorted(found, key=lambda item: (item["day"], str(item["label"])))


def describe(label: str | None) -> str | None:
    if label is None:
        return None
    match = TERM.search(label)
    if match is None:
        return label.lower()
    month, day, year = (int(part) for part in match.group(2).split("/"))
    when = date(year, month, day).strftime("%B %-d, %Y")
    return f"term from {when}" if match.group(1) == "Term Commencing" else f"unexpired term ending {when}"


def emit():
    rows = contests()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio Supreme Court contests 2000 to 2020 by county from the Secretary of State's official results, "
            "with 2008 statewide totals from the official canvass",
            polity="US-OH",
        )
        sources: dict[str, UUID] = {}
        for item in rows:
            url = str(item["url"])
            if url in sources:
                continue
            title = f"{item['day'].year} general election official results: {url.rsplit('/', 1)[-1]}"
            sources[url] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=PAGE,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%' and length(u.code) = 5"""
            ).fetchall()
        }
        state_area, state_unit, polity = conn.execute(
            """select u.area_id, u.id, p.id from polity p
               join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state' where p.iso_code = 'US-OH'"""
        ).fetchone()
        taken = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.path like 'us/oh/%%' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        fptp = cs.key("us.electoral_system", "fptp")
        known: dict[tuple[str, str], UUID] = {}
        for person, text in conn.execute(
            """select cy.person_id, n.text from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ ':OH:(chief-)?justice-of-the-supreme-court'"""
        ).fetchall():
            known[identity(text)] = person
        persons: set[str] = set()
        for item in rows:
            day, office_title, label = item["day"], str(item["office"]), item["label"]
            places: dict[str, dict[str, int]] = item["places"]
            url = str(item["url"])
            office = cs.key("us.office", f"{slug(office_title)}:OH")
            if office in cs.minted:
                cs.add("office", id=office, polity_id=polity, kind="other")
                cs.add(
                    "name",
                    entity_id=office,
                    text=f"{office_title} of Ohio",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
            election = cs.key("us.election", f"{day}:OH")
            suffix = f":{slug(str(label))}" if label is not None else ""
            contest_key = f"{day}:OH:{slug(office_title)}{suffix}"
            contest = cs.key("us.contest", contest_key)
            remainder = label is not None and label.startswith("Unexpired")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=state_area,
                term="remainder" if remainder else "full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            detail = describe(None if label is None else str(label))
            cs.add(
                "name",
                entity_id=contest,
                text=f"{day.year} Ohio {office_title} election" + (f" ({detail})" if detail else ""),
                lang="en",
                kind="official",
                is_preferred=True,
            )
            path = f"us/oh/{day}-general/{slug(office_title)}" + (f"-{slug(str(label))}" if label is not None else "")
            assert path not in taken, path
            taken.add(path)
            cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=sources[url])
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[url],
                is_default=True,
                completeness=1,
            )
            names = list(next(iter(places.values())))
            totals = {name: sum(place[name] for place in places.values()) for name in names}
            printed = item.get("printed", totals)
            caveat = " ".join(
                CAVEAT.format(name=name, summed=totals[name], printed=printed[name])
                for name in names
                if totals[name] != printed[name]
            )
            body = NOTE_STATE if url == CANVASS_2008 else NOTE
            cs.add("note", entity_id=result, kind="methodology", body_md=f"{body} {caveat}".strip())
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            for position, name in enumerate(names):
                first, last = identity(name)
                person_key = f"OH:{slug(first)}-{slug(last)}"
                person = known.get((first, last)) or cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add(
                            "name", entity_id=person, text=display(name), lang="en", kind="official", is_preferred=True
                        )
                options[name] = cs.key("us.option", f"{contest_key}:{slug(name)}")
                cs.add(
                    "contest_option",
                    id=options[name],
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position + 1,
                    ballot_label=display(name),
                )
                candidacies[name] = cs.key("us.candidacy", f"{contest_key}:{slug(name)}")
                cs.add(
                    "candidacy",
                    id=candidacies[name],
                    person_id=person,
                    option_id=options[name],
                    role="head",
                    ballot_name=display(name),
                    is_independent=False,
                )
            winner = max(totals, key=totals.__getitem__)
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options[winner],
                candidacy_id=None,
                kind="elected",
                seats=1,
            )
            values = []
            for place, votes in places.items():
                unit = state_unit if place == "Ohio" else counties[flat(place)]
                for name, count_ in votes.items():
                    values.append((str(result), str(unit), str(options[name]), 0, 1, 0, 0, count_))
                values.append((str(result), str(unit), None, 0, 4, 0, 0, sum(votes.values())))
            cs.frame(
                "result_value",
                pl.DataFrame(
                    values,
                    schema={
                        "result_set_id": pl.String,
                        "unit_id": pl.String,
                        "option_id": pl.String,
                        "channel": pl.Int16,
                        "measure": pl.Int16,
                        "count_round": pl.Int16,
                        "flags": pl.Int16,
                        "value": pl.Int64,
                    },
                    orient="row",
                ),
            )
            print(contest_key, len(places), totals)
        return cs.write()
