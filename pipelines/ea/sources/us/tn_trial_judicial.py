import re
import subprocess
import unicodedata
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.retention import display, identity
from ea.store import DATA, connect

SOURCE = "us.tn_trial_judicial"
VERSION = "1"
INBOX = DATA / "inbox" / "manual" / "MF-470"
URL = "https://sos.tn.gov/elections/results"
PUBLISHER = "Tennessee Secretary of State, Division of Elections"
TITLE = "Trial court, district attorney, and public defender results by county"
FILES = {
    "general.pdf": date(2002, 8, 1),
    "Gen115Judicial.pdf": date(2006, 8, 3),
    "Gen1631Judicial.pdf": date(2006, 8, 3),
    "CCStateGeneralCountyTotals.pdf": date(2010, 8, 5),
    "180802_GenbyCounty.pdf": date(2018, 8, 2),
}
OFFICES = {
    "circuit and criminal court judge": "Circuit and Criminal Court Judge",
    "circuit court judge": "Circuit Court Judge",
    "circuit court": "Circuit Court Judge",
    "criminal court judge": "Criminal Court Judge",
    "criminal court": "Criminal Court Judge",
    "chancellor": "Chancellor",
    "probate court judge": "Probate Court Judge",
    "district attorney general": "District Attorney General",
    "public defender": "Public Defender",
}
PARTIES = {
    "d": "democratic-party",
    "democratic": "democratic-party",
    "r": "republican-party",
    "republican": "republican-party",
}
HEADING = re.compile(
    r"^(?P<office>Circuit and Criminal Court Judge|Circuit Court Judge|Circuit Court|Criminal Court Judge|Criminal Court"
    r"|Chancellor|Probate Court Judge|District Attorney General|Public Defender)"
    r"(?P<seat>[ ,].*?)?[ ,]+District\s+(?P<district>\d+)\s*(?P<unexpired>\(unexpired term\))?$"
)
CANDIDATE = re.compile(r"(\d+)\s*\.?\s+(.+?)(?=\s{3,}\d+\s*\.\s|\s*$)")
INDEX = re.compile(r"^\s*\d+(?:\s+\d+)*\s*$")
ROW = re.compile(r"^([A-Za-z][A-Za-z .'-]*?)\s{2,}((?:[\d,]+\s*)+)$")
TOTAL = re.compile(r"^(?:DISTRICT|STATE|STATEWIDE)\s+TOTALS?\b", re.IGNORECASE)
SKIP = re.compile(
    r"^(?:(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}"
    r"|General Election|State General|State of Tennessee)$|Page \d+ of \d+|^\d{1,2}-[A-Za-z]{3}-\d{2}\s"
)
METHOD = (
    "County totals from the Tennessee Division of Elections' official county results for the August general election. "
    "The district is made of the counties listed."
)
MARK = (
    "The results file marks some candidates with a bare I and does not say whether it means incumbent or independent, "
    "so no party is recorded for them."
)


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def fetch() -> None:
    for name in FILES:
        try:
            raw.cached(SOURCE, f"{URL}#{name}")
        except LookupError:
            raw.store(
                SOURCE,
                f"{URL}#{name}",
                (INBOX / name).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def seat_label(text: str) -> str:
    text = re.sub(r"\bDivison\b", "Division", (text or "").strip(" ,"))
    ordinal = re.match(r"^(\d+)(?:st|nd|rd|th) Division$", text)
    if ordinal is not None:
        return f"Division {ordinal[1]}"
    return text


def candidate(text: str, day: date) -> tuple[str, str | None, bool, bool]:
    text = text.strip()
    if day.year == 2002:
        text = re.sub(r"\s*\(I\)$", "", text)
        if text.startswith("Write-Ins"):
            return "Write-Ins", None, False, False
        found = re.match(r"^([^,]+), (Jr\.|Sr\.|II|III|IV), (.+)$", text)
        if found is not None:
            text = f"{found[3]} {found[1]} {found[2]}"
        else:
            found = re.match(r"^([^,]+), (.+)$", text)
            if found is not None:
                text = f"{found[2]} {found[1]}"
        return text, None, False, False
    mark = re.match(r"^(.+?)\s+-\s+(D|R|I|Democratic|Republican|Independent)$", text)
    if mark is None:
        return text, None, False, False
    value = mark[2].lower()
    return mark[1], PARTIES.get(value), value == "independent", value == "i"


def blocks(path: Path, day: date) -> list[dict[str, object]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    text = re.sub(r"Judicial District\s*\n\s*(\d+)", r"District \1", text)
    text = re.sub(r"(District Attorney General)\s*\n\s*(\d+)\s*\n", r"\1 District \2\n", text)
    found: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or SKIP.search(stripped):
            continue
        heading = HEADING.match(stripped)
        if heading is not None:
            current = {
                "office": OFFICES[heading["office"].lower()],
                "seat": seat_label(heading["seat"]),
                "district": int(heading["district"]),
                "unexpired": heading["unexpired"] is not None,
                "candidates": [],
                "numbered": [],
                "rows": {},
                "totals": None,
            }
            found.append(current)
            continue
        if current is None:
            continue
        if INDEX.match(line):
            continue
        if TOTAL.match(stripped):
            values = re.findall(r"[\d,]+", stripped)
            current["totals"] = [int(value.replace(",", "")) for value in values]
            continue
        row = ROW.match(line)
        if row is not None and current["candidates"]:
            current["rows"][row[1].strip()] = [int(value.replace(",", "")) for value in row[2].split()]
            continue
        if current["rows"]:
            current = None
            continue
        for number, name in CANDIDATE.findall(line):
            current["numbered"].append((int(number), candidate(name, day)))
            current["candidates"] = [value for _, value in sorted(current["numbered"])]
    merged: dict[tuple[object, ...], dict[str, object]] = {}
    for block in found:
        key = (block["office"], block["seat"], block["district"], block["unexpired"], tuple(block["candidates"]))
        if key not in merged:
            merged[key] = block
            continue
        merged[key]["rows"].update(block["rows"])
        merged[key]["totals"] = merged[key]["totals"] or block["totals"]
    clean = []
    for block in merged.values():
        if not block["rows"]:
            continue
        width = len(block["candidates"])
        if any(len(values) != width for values in block["rows"].values()):
            raise ValueError(f"{path.name}: {block['office']} {block['district']}: row width differs from {width}")
        sums = [sum(values[index] for values in block["rows"].values()) for index in range(width)]
        if block["totals"] != sums:
            raise ValueError(f"{path.name}: {block['office']} {block['district']}: totals {block['totals']} != {sums}")
        clean.append({**block, "day": day, "file": path.name})
    return clean


def contests() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for name, day in FILES.items():
        found.extend(blocks(raw.cached(SOURCE, f"{URL}#{name}").path, day))
    return found


def districts() -> dict[int, list[str]]:
    seen: dict[int, dict[date, set[str]]] = defaultdict(lambda: defaultdict(set))
    for block in contests():
        seen[block["district"]][block["day"]] |= {place.title() for place in block["rows"]}
    result = {}
    for district, by_day in sorted(seen.items()):
        union = set().union(*by_day.values())
        for day, places in by_day.items():
            if places != union:
                print(f"district {district}: {day} lacks {sorted(union - places)}")
        result[district] = sorted(union)
    return result


def ordinal(number: int) -> str:
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(number if number < 20 else number % 10, "th")
    return f"{number}{suffix}"


def emit() -> Path:
    found = contests()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Tennessee circuit, criminal, chancery, and probate court judges, district attorneys general, "
            "and public defenders in the August general elections of 2002, 2006, 2010, and 2018, by county",
            polity="US-TN",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, f"{URL}#{name}"), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '47%%'"""
            ).fetchall()
        }
        state_area, state_polity = conn.execute(
            """select u.area_id, p.id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-TN'"""
        ).fetchone()
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/tn/%%'").fetchall()}
        fptp = cs.key("us.electoral_system", "fptp")

        def name(entity: UUID, text: str) -> None:
            existing = conn.execute(
                """select n.id, n.text, c.pipeline from name n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and n.is_preferred""",
                [entity],
            ).fetchone()
            if existing is not None and (existing[1] == text or existing[2] != SOURCE):
                return
            if existing is not None:
                cs.add("name_drop", name_id=existing[0], entity_id=entity, text=existing[1], reason="Name restyled")
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        made_districts: dict[int, tuple[UUID, UUID]] = {}
        for number in sorted(districts()):
            label = f"{ordinal(number)} Judicial District"
            code = f"TN:{number}"
            area = cs.area("us.judicial_district", code)
            polity = cs.key("us.polity", f"judicial:{code}")
            made_districts[number] = (polity, area)
            if polity not in cs.minted:
                continue
            cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
            name(polity, f"Tennessee {label}")
            cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
            path = f"us/tn/{slug(label)}"
            if path in taken:
                raise ValueError(f"{label} would take {path}")
            taken.add(path)
            cs.add("slug", path=path, entity_id=polity)
            cs.add("area", id=area, scheme_code="us.judicial_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=state_area)
            name(area, f"{label}, TN")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "reporting_unit",
                id=cs.unit("us.judicial_district", code),
                polity_id=polity,
                scheme_code="us.judicial_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        offices: set[UUID] = set()
        persons: set[UUID] = set()
        memberships: set[tuple[UUID, UUID, date]] = set()
        made = 0
        for block in found:
            day, number, title, seat = block["day"], block["district"], block["office"], block["seat"]
            polity, area = made_districts[number]
            label = f"{ordinal(number)} Judicial District"
            office = cs.key("us.office", f"{slug(title)}:TN:{number}")
            if office not in offices:
                offices.add(office)
                cs.add("office", id=office, polity_id=polity, kind="other")
                if office in cs.minted:
                    name(office, f"{title}, Tennessee {label}")
            election = cs.key("us.election", f"{day}:TN:primary")
            contest_key = f"{day}:TN:{slug(title)}:{number}" + (f":{slug(seat)}" if seat else "")
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                term="remainder" if block["unexpired"] else "full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            shown = f"{title}" + (f", {seat}" if seat else "")
            name(contest, f"{day.year} {shown}, Tennessee {label}")
            if contest in cs.minted:
                base = f"us/tn/{day}-primary/{slug(title)}-{number}" + (f"-{slug(seat)}" if seat else "")
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
            marked = any(bare for *_, bare in block["candidates"])
            cs.add("note", entity_id=result, kind="methodology", body_md=f"{METHOD} {MARK}" if marked else METHOD)
            options: dict[int, UUID] = {}
            candidacies: dict[int, UUID] = {}
            for position, (full, party, independent, _) in enumerate(block["candidates"]):
                write_in = re.match(r"^Write-Ins?\b[,:]?\s*\.?$", full) is not None
                named_write_in = re.match(r"^Write-In: (.+)$", full)
                person_name = named_write_in[1] if named_write_in is not None else full
                options[position] = cs.key("us.option", f"{contest_key}:{position + 1}")
                if write_in:
                    cs.add(
                        "contest_option",
                        id=options[position],
                        contest_id=contest,
                        kind="write_in",
                        ballot_position=position + 1,
                        ballot_label="Write-in",
                    )
                    continue
                first, last = identity(person_name)
                person = cs.key(f"{SOURCE}.person", f"TN:{number}:{slug(first)}-{slug(last)}")
                if person not in persons:
                    persons.add(person)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, display(person_name))
                cs.add(
                    "contest_option",
                    id=options[position],
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
                    ballot_position=position + 1,
                    ballot_label=display(full) if named_write_in is None else f"Write-in: {display(person_name)}",
                )
                candidacies[position] = cs.key("us.candidacy", f"{contest_key}:{position + 1}")
                cs.add(
                    "candidacy",
                    id=candidacies[position],
                    person_id=person,
                    option_id=options[position],
                    role="head",
                    ballot_name=display(person_name),
                    is_independent=independent,
                )
            totals = block["totals"]
            ranked = [index for index in range(len(totals)) if index in candidacies]
            winner = max(ranked, key=totals.__getitem__)
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options[winner],
                candidacy_id=candidacies[winner],
                kind="elected",
                seats=1,
            )
            values = []
            for place, votes in block["rows"].items():
                unit = counties[flat(place)]
                memberships.add((unit, area, day))
                for position, count in enumerate(votes):
                    values.append((str(result), str(unit), str(options[position]), 0, 1, 0, 0, count))
                values.append((str(result), str(unit), None, 0, 4, 0, 0, sum(votes)))
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
            made += 1
        for unit, area, day in memberships:
            cs.add("reporting_unit_area", unit_id=unit, area_id=area, valid_from=day, valid_to=day + timedelta(days=1))
        print(made, "contests,", len(made_districts), "districts")
        return cs.write()
