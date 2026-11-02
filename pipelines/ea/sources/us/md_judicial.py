import html
import re
from datetime import date
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display
from ea.store import connect
from ea.typed import one

SOURCE = "us.md_judicial"
VERSION = "1"
PAGE = "https://msa.maryland.gov/msa/mdmanual/42electg/html/{year}/{name}.html"
PUBLISHER = "Maryland State Archives"
TITLE = "Maryland Manual On-Line: General Election Returns, Judges"
DAYS = {
    2000: date(2000, 11, 7),
    2002: date(2002, 11, 5),
    2004: date(2004, 11, 2),
    2006: date(2006, 11, 7),
    2008: date(2008, 11, 4),
    2010: date(2010, 11, 2),
    2012: date(2012, 11, 6),
    2014: date(2014, 11, 4),
    2022: date(2022, 11, 8),
}
SHORT = {2000: "00jud", 2004: "04jud"}
CIRCUITS = {(2014, "Shirley M. Watts*"): 6}
SPELLING = {"Nazarin": "Nazarian"}
LABELS = {"for", "against", "yes", "yes*", "no"}
COUNTY = re.compile(r"(?:County|Co\.|Baltimore City|St\. Mary's|Baltimore)$")
TITLES = {"appeals": "Judge of the Court of Appeals", "special": "Judge of the Court of Special Appeals"}
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "The judge keeps the seat when a majority votes Yes."
)
NOTE = "Returns from the Maryland Manual On-Line's page of the general election's judges, compiled by the State Board of Elections."


def url(year: int) -> str:
    return PAGE.format(year=year, name=SHORT.get(year, f"{year}jud"))


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, url(year), suffix=".html")


def cells(year: int) -> list[str]:
    text = raw.cached(SOURCE, url(year)).path.read_text(errors="ignore")
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>|<!--.*?-->", "", text)
    text = re.sub(r"(?i)</t[dr]>|<br\s*/?>|</p>|</h\d>", "\n", text)
    lines = [
        re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()
        for line in html.unescape(re.sub(r"<[^>]+>", " ", text)).split("\n")
    ]
    return [line for line in lines if line]


def judges(year: int) -> list[dict[str, Any]]:
    lines = cells(year)
    start = next(
        i for i, x in enumerate(lines) if re.match(r"(FOR )?COURT OF (SPECIAL )?APPEALS", x) and x.upper() == x
    )
    end = next(
        (i for i, x in enumerate(lines) if i > start and re.match(r"(FOR )?CIRCUIT COURT", x) and x.upper() == x),
        len(lines),
    )
    court = circuit = None
    found: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending: list[int] | None = None
    for x in lines[start:end]:
        up = x.upper()
        if re.match(r"(FOR )?COURT OF (SPECIAL )?APPEALS", up) and x == up:
            court = "special" if "SPECIAL" in up else "appeals"
            continue
        match = re.match(r"(\d+)(?:ST|ND|RD|TH) APPELLATE CIRCUIT", up)
        if match is not None:
            circuit, current = int(match[1]), None
            continue
        if up == "AT LARGE":
            circuit, current = 0, None
            continue
        if up.startswith("(FOR CONTINUANCE") or "%" in x or x.lower() in LABELS:
            continue
        if re.fullmatch(r"[\d,]+", x):
            number = int(x.replace(",", ""))
            if pending is not None:
                pending.append(number)
            elif current is not None:
                current["nums"].append(number)
            continue
        if up == "TOTAL" and current is not None:
            pending = current["total"] = []
            continue
        if COUNTY.search(x) and current is not None:
            pending = current["counties"].setdefault(x, [])
            continue
        current = {"court": court, "circuit": circuit, "name": x, "nums": [], "counties": {}, "total": None}
        pending = None
        found.append(current)
    for item in found:
        item["circuit"] = CIRCUITS.get((year, item["name"]), item["circuit"])
        counties: dict[str, list[int]] = item["counties"]
        if counties:
            yes = sum(v[0] for v in counties.values())
            no = sum(v[1] for v in counties.values())
            item["printed"] = item["total"] if item["total"] not in (None, [yes, no]) else None
            if item["printed"] is not None and max(abs(a - b) for a, b in zip(item["printed"], [yes, no])) > 100:
                raise ValueError(f"{year} {item['name']}: counties {yes}, {no} against total {item['total']}")
            item["nums"] = [yes, no]
        if len(item["nums"]) != 2 or item["circuit"] is None:
            raise ValueError(f"{year} {item['name']}: {item['nums']} circuit {item['circuit']}")
    return found


def emit() -> object:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '24%%'"""
            ).fetchall()
        }
        fips["baltimore"] = "24005"
        fips["baltimorecity"] = "24510"
        state_unit, state_area, polity = one(
            conn.execute(
                """select u.id, u.area_id, p.id from polity p
               join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-MD'"""
            )
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/md/%%'").fetchall()}
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland Court of Appeals and Court of Special Appeals retention elections 2000–2022 "
            "from the Maryland Manual On-Line",
            polity="US-MD",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE.format(year=2010, name="2010jud"),
            license="Public record",
        )
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        retention_system = cs.key("us.electoral_system", "retention")

        def name(entity: UUID, text: str) -> None:
            found = conn.execute(
                """select n.id, n.text, c.pipeline from name n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and n.is_preferred""",
                [entity],
            ).fetchone()
            if found is not None and (found[1] == text or found[2] != SOURCE):
                return
            if found is not None:
                cs.add("name_drop", name_id=found[0], entity_id=entity, text=found[1], reason="Name restyled")
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        districts: dict[int, UUID] = {}
        units: dict[int, UUID] = {}
        offices: set[UUID] = set()
        persons: set[str] = set()
        for year, day in DAYS.items():
            election = cs.key("us.election", f"{day}:MD")
            if election in cs.minted:
                raise ValueError(f"{day}: no Maryland general election")
            for row in judges(year):
                title = TITLES[str(row["court"])]
                raw_name = re.sub(r"\*", "", str(row["name"])).strip()
                for wrong, right in SPELLING.items():
                    raw_name = raw_name.replace(wrong, right)
                shown = display(raw_name)
                tokens = [
                    t
                    for t in re.sub(r"[.,]", " ", raw_name).split()
                    if len(t) > 1 and t.lower() not in {"jr", "sr", "ii", "iii"}
                ]
                person_key = f"MD:{slug(tokens[0] + ' ' + tokens[-1])}"
                office_key = f"{slug(title)}:MD"
                office = cs.key("us.office", office_key)
                if office not in offices:
                    offices.add(office)
                    if office in cs.minted:
                        cs.add("office", id=office, polity_id=polity, kind="other")
                        name(office, f"{title} of Maryland")
                circuit = int(row["circuit"])
                if circuit == 0:
                    area, unit = state_area, state_unit
                else:
                    if circuit not in districts:
                        districts[circuit] = cs.area("us.judicial_district", f"MD:appellate-{circuit}")
                        if districts[circuit] in cs.minted:
                            raise ValueError(f"no appellate circuit {circuit}")
                        units[circuit] = cs.unit("us.aggregate", f"MD:appellate-{circuit}")
                        if units[circuit] in cs.minted:
                            cs.add(
                                "reporting_unit",
                                id=units[circuit],
                                polity_id=polity,
                                scheme_code="us.aggregate",
                                code=f"MD:appellate-{circuit}",
                                area_relation="equals",
                                area_id=districts[circuit],
                                kind="aggregate",
                                valid_from=date(1980, 1, 1),
                            )
                    area, unit = districts[circuit], units[circuit]
                suffix = person_key.split(":")[1]
                contest_key = f"{day}:MD:{slug(title)}:{suffix}"
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
                    electoral_system_id=retention_system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Maryland {title} retention: {shown}")
                if contest in cs.minted:
                    base = f"us/md/{day}-general/{slug(title)}-{suffix}"
                    path, count = base, 1
                    while path in taken:
                        count += 1
                        path = f"{base}-{count}"
                    taken.add(path)
                    cs.add("slug", path=path, entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=source)
                result = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result, kind="methodology", body_md=f"{METHOD} {NOTE}")
                yes, no = row["nums"]
                if row.get("printed") is not None:
                    cs.add(
                        "note",
                        entity_id=result,
                        kind="caveat",
                        body_md=f"The county rows add to {yes:,} Yes and {no:,} No; the page's printed total is "
                        f"{row['printed'][0]:,} Yes and {row['printed'][1]:,} No. The county rows are shown as printed.",
                    )
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, shown)
                options: dict[str, UUID] = {}
                for position, answer in enumerate(("yes", "no")):
                    options[answer] = cs.key("us.option", f"{contest_key}:{answer}")
                    cs.add(
                        "contest_option",
                        id=options[answer],
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position + 1,
                        ballot_label=answer.capitalize(),
                    )
                candidacy = cs.key("us.candidacy", f"{contest_key}:yes:head")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=options["yes"],
                    role="head",
                    ballot_name=shown,
                    is_independent=False,
                )
                retained = yes > no
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options["yes"],
                    candidacy_id=None,
                    kind="elected" if retained else "lost",
                    seats=1 if retained else None,
                )
                tallies: list[tuple[UUID, int, int]] = []
                if row["counties"]:
                    for county, (cy, cn) in row["counties"].items():
                        key = re.sub(r"[^a-z]", "", county.lower())
                        key = re.sub(r"(county|co)$", "", key) if key != "baltimorecity" else key
                        tallies.append((cs.unit("us.county", fips[key], day), cy, cn))
                else:
                    tallies.append((unit, yes, no))
                values = []
                for tally_unit, cy, cn in tallies:
                    values += [
                        (str(result), str(tally_unit), str(options["yes"]), 0, 1, 0, 0, cy),
                        (str(result), str(tally_unit), str(options["no"]), 0, 1, 0, 0, cn),
                        (str(result), str(tally_unit), None, 0, 4, 0, 0, cy + cn),
                    ]
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
                print(contest_key, yes, no, len(tallies))
        return cs.write()
