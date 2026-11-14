import csv
import io
import json
import re
import subprocess
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any, LiteralString
from uuid import UUID

import shapefile
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import guam_gec_precincts as precincts
from ea.store import connect

SOURCE = "us.guam_gec_contests"
VERSION = "1"
PUBLISHER = precincts.PUBLISHER
COUSUB = "https://www2.census.gov/geo/tiger/TIGER2020/COUSUB/tl_2020_66_cousub.zip"
VILLAGES = {
    "asanmaina": "asan",
    "santaritasumai": "santarita",
    "tamuningtumonharmon": "tamuning",
    "hagat": "agat",
    "inalahan": "inarajan",
    "malesso": "merizo",
    "humatak": "umatac",
}
JUDGES = {
    "bordallo": ("Michael J. Bordallo", "superior"),
    "torres": ("Robert J. Torres Jr.", "supreme"),
    "perez": ("Vernon P. Perez", "superior"),
    "maraman": ("Katherine A. Maraman", "supreme"),
    "sukola": ("Anita A. Sukola", "superior"),
    "carbullido": ("F. Philip Carbullido", "supreme"),
    "lamorena": ("Alberto C. Lamorena III", "superior"),
    "barcinas": ("Arthur R. Barcinas", "superior"),
    "cenzon": ("Maria T. Cenzon", "superior"),
    "iriarte": ("Elyze M. Iriarte", "superior"),
}
COURTS = {
    "supreme": ("justice-of-the-supreme-court:GU", "Justice of the Supreme Court of Guam", "Supreme Court"),
    "superior": ("judge-of-the-superior-court:GU", "Judge of the Superior Court of Guam", "Superior Court"),
}
BODIES = {
    "legislature": ("Guam Legislature", "legislature"),
    "education board": ("Guam Education Board", "education-board"),
    "consolidated commission on utilities": (
        "Guam Consolidated Commission on Utilities",
        "consolidated-commission-on-utilities",
    ),
}
ALIASES = {
    "morrison:tommy": "morrison:thomas",
    "castro:wil": "castro:william",
    "espaldon:jim": "espaldon:james",
    "marshtaitano:kelly": "marsh:kelly",
    "ada:tony": "ada:vicente",
}
PARTIES = {"DEM": "democratic-party", "REP": "republican-party"}
SUFFIXES = {"JR", "SR", "II", "III", "IV"}
REFERENDUM = (
    "2014 Guam Proposal 14A (Compassionate Cannabis Use Act)",
    "proposal-14a",
)

Ballots = dict[tuple[date, str], dict[str, object]]


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def one(conn: Connection, query: LiteralString, params: list[object]) -> tuple[Any, ...]:
    row = conn.execute(query, params).fetchone()
    if row is None:
        raise LookupError(query)
    return row


def fetch() -> None:
    raw.fetch(SOURCE, COUSUB, suffix=".zip")


def record(ballots: Ballots, day: date, heading: str, vote_for: int | None, code: str, label: str, votes: int) -> None:
    entry = ballots.setdefault((day, heading), {"vote_for": vote_for, "units": {}})
    units: dict[str, dict[str, int]] = entry["units"]  # ty: ignore[invalid-assignment]
    units.setdefault(code, {})[label] = votes


def parse(artifacts: dict[date, list[raw.Artifact]]) -> Ballots:
    ballots: Ballots = {}
    for year, (day, pattern) in precincts.REPORTS.items():
        for number in range(1, 81):
            try:
                artifact = raw.cached(precincts.SOURCE, pattern.format(number))
            except LookupError:
                continue
            artifacts.setdefault(day, []).append(artifact)
            lines = re.sub(r"<[^>]+>", "", artifact.path.read_text(encoding="latin-1")).splitlines()
            heading, vote_for = None, None
            for index, line in enumerate(lines):
                limit = re.match(r"\s*(?:VOTE FOR|Mark no more than)\s+(\d+)", line)
                if limit:
                    heading, vote_for = re.sub(r"^NON ", "", lines[index - 1].strip()), int(limit[1])
                    continue
                found = re.match(r"^\s*(?:\d+\.\s+)?(.*?)[\s.]*\s(\d[\d,]*)\s+[\d.]+\s*$", line)
                if found and heading and not re.match(r"(?i)(total|over votes|under votes)", found[1].strip()):
                    record(
                        ballots,
                        day,
                        heading,
                        vote_for,
                        f"{year}:GU:{number:04d}",
                        found[1].strip(" ."),
                        int(found[2].replace(",", "")),
                    )
    for year, (day, listing) in precincts.DRIVE.items():
        for link in precincts.drive_links(listing):
            artifact = raw.cached(precincts.SOURCE, link)
            artifacts.setdefault(day, []).append(artifact)
            text = subprocess.run(
                ["pdftotext", "-layout", str(artifact.path), "-"], capture_output=True, check=True, text=True
            ).stdout
            label = re.search(r"(?m)^Precinct (\S+)", text)
            if label is None:
                raise ValueError(f"{link}: no precinct label")
            code = f"{year}:GU:{label[1]}"
            lines = text.splitlines()
            heading, vote_for, last = None, None, None
            for index, line in enumerate(lines):
                stripped = line.strip()
                limit = re.match(r"Vote For (\d+)$", stripped)
                if limit:
                    heading, vote_for, last = lines[index - 1].strip(), int(limit[1]), None
                    continue
                found = re.match(r"^(.*?)\s{2,}(\d[\d,]*)\s+[\d.]+%$", stripped)
                if found and heading and not found[1].startswith("Total Votes"):
                    last = found[1].strip()
                    record(ballots, day, heading, vote_for, code, last, int(found[2].replace(",", "")))
                elif stripped and heading and last and not found and not re.search(r"\d", stripped):
                    units: dict[str, dict[str, int]] = ballots[(day, heading)]["units"]  # ty: ignore[invalid-assignment]
                    votes = units[code].pop(last)
                    last = f"{last} {stripped}"
                    units[code][last] = votes
                else:
                    last = None
    day, url = precincts.CSV24
    artifact = raw.cached(precincts.SOURCE, url)
    artifacts.setdefault(day, []).append(artifact)
    grid = list(csv.reader(artifact.path.read_text(encoding="utf-8-sig").splitlines()))
    for row in grid[3:]:
        if not row[1].strip().isdigit():
            continue
        for index in range(6, len(row)):
            label = grid[2][index]
            if label.endswith("VOTES") or not grid[0][index]:
                continue
            record(ballots, day, grid[0][index], None, f"2024:GU:{int(row[1]):04d}", label, int(row[index] or 0))
    totals = next(row for row in grid if row[1] == "ZZZ")
    for (when, heading), entry in ballots.items():
        if when != day:
            continue
        columns = [index for index in range(6, len(grid[0])) if grid[0][index] == heading]
        marks = sum(int(totals[index] or 0) for index in columns)
        under = [int(totals[index] or 0) for index in columns if grid[2][index] == "UNDER VOTES"]
        cast = sum(
            int(row[4] or 0)
            for row in grid[3:]
            if row[1].strip().isdigit() and any(int(row[index] or 0) for index in columns)
        )
        entry["vote_for"] = round(marks / cast) if under and cast else 1
    return ballots


def classify(heading: str) -> tuple[str, str] | None:
    lowered = re.sub(r"\s+", " ", heading.lower())
    for name in BODIES:
        if name in lowered:
            return "body", name
    if "attorney general" in lowered:
        return "attorney-general", ""
    if "public auditor" in lowered:
        return "public-auditor", ""
    if "referendum" in lowered:
        return "referendum", ""
    if "retain" in lowered or "retention" in lowered:
        judge = next(name for name in JUDGES if name in lowered)
        return "retention", judge
    if "mayor" in lowered:
        village = re.sub(r"(?i)\b(vice[- ]mayor|mayor|district)\b", " ", heading)
        village = fold(re.sub(r"\s+", " ", village).strip().split("  ")[0])
        half = len(village) // 2
        if village[:half] == village[half:]:
            village = village[:half]
        return ("vice-mayor" if "vice" in lowered else "mayor"), VILLAGES.get(village, village)
    return None


def case(word: str) -> str:
    if "." in word or not re.search(r"[A-ZÑ]{2}", word):
        return word
    return "-".join(
        re.sub(r"^Mc([a-z])", lambda found: "Mc" + found[1].upper(), piece.capitalize()) for piece in word.split("-")
    )


def person_name(label: str) -> tuple[str, str, str | None, bool]:
    tail = re.search(r"\s*\((DEM|REP|IND)\)?\s*$", label.strip())
    party = tail[1] if tail else None
    text = label.strip()[: tail.start()] if tail else label.strip()
    prefix = re.match(r"^(DEM|REP|IND|NPP)\s+", text)
    if prefix:
        party, text = prefix[1], text[prefix.end() :]
    incumbent = bool(re.search(r"\s+INC\.?\s*$", text))
    text = re.sub(r"\s+INC\.?\s*$", "", text)
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) == 1:
        words = parts[0].split()
        split = next((index for index, word in enumerate(words) if not re.search(r"[A-ZÑ]{2}", word)), len(words))
        parts = [" ".join(words[:split]), " ".join(words[split:])]
    surname_words = parts[0].split()
    words = []
    suffix = ""
    for word in [*surname_words[1:], *(word for part in parts[1:] for word in part.split())]:
        if word.rstrip(".").upper() in SUFFIXES:
            suffix = word.rstrip(".")
    surname_words = [surname_words[0]] + [w for w in surname_words[1:] if w.rstrip(".").upper() not in SUFFIXES]
    words = [w for part in parts[1:] for w in part.split() if w.rstrip(".").upper() not in SUFFIXES]
    if words and re.fullmatch(r"(?i)dr\.?|phd", words[0]):
        words = words[1:]
    surname = " ".join(case(word) for word in surname_words)
    first = " ".join(case(word) for word in words)
    suffix = suffix.capitalize() + "." if suffix.upper() in ("JR", "SR") else suffix.upper()
    display = " ".join(part for part in (first, surname, suffix) if part)
    key = f"{fold(surname)}:{fold(words[0]) if words else ''}"
    return display, ALIASES.get(key, key), party, incumbent


def emit() -> Path:
    artifacts: dict[date, list[raw.Artifact]] = {}
    ballots = parse(artifacts)
    with zipfile.ZipFile(raw.cached(SOURCE, COUSUB).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    villages: dict[str, tuple[str, str, str]] = {}
    for item in reader.iterShapeRecords():
        if item.record is None or item.shape is None:
            raise ValueError("county subdivision without a record or shape")
        fields = item.record.as_dict()
        if fields["FUNCSTAT"] == "N":
            villages[fold(str(fields["NAME"]))] = (
                str(fields["GEOID"]),
                str(fields["NAME"]),
                json.dumps(item.shape.__geo_interface__),
            )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Guam Legislature, Attorney General, Public Auditor, Education Board, Consolidated Commission on Utilities, mayors, vice mayors, judicial retention, and the 2014 referendum, general elections 2014 to 2024 by precinct from the Guam Election Commission",
            polity="US-GU",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-GU")
        parent, territory = one(
            conn, "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        )
        county = cs.area("us.county", "66010")
        census = cs.source("U.S. Census Bureau", "TIGER/Line Shapefiles: County Subdivisions")
        if census in cs.minted:
            raise ValueError("the Census Bureau's county subdivision source must already exist")
        cs.artifact(raw.cached(SOURCE, COUSUB), census)
        shapes = {
            code: (geom, point)
            for code, geom, point in conn.execute(
                """select f.code, encode(st_asewkb(s.geom), 'hex'), encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
                   from unnest(%s::text[], %s::text[]) as f(code, geojson)
                   cross join lateral (select st_multi(st_collectionextract(st_makevalid(st_transform(
                     st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326)), 3)) as geom) s""",
                [[v[0] for v in villages.values()], [v[2] for v in villages.values()]],
            ).fetchall()
        }
        places: dict[str, tuple[UUID, UUID, str]] = {}
        for key, (code, title, _) in villages.items():
            area = cs.area("us.town", code)
            polity = cs.key("us.polity", f"town:{code}")
            places[key] = (area, polity, title)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="us.town")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=county)
                name(area, f"{title}, GU")
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=None,
                    geom=shapes[code][0],
                    label_point=shapes[code][1],
                    quality="official",
                    source_id=census,
                )
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                name(polity, title)
                cs.add("polity_parent", polity_id=polity, parent_id=state)
                cs.add("slug", path=f"us/gu/{slug(title)}", entity_id=polity)
                cs.add("polity_territory", polity_id=polity, area_id=area)
            unit = cs.unit("us.town", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="us.town",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
        bodies = {
            key: one(
                conn,
                "select b.id from body b join name n on n.entity_id = b.id where b.polity_id = %s and n.text = %s",
                [state, title],
            )[0]
            for key, (title, _) in BODIES.items()
        }
        offices: dict[str, UUID] = {}
        for what, value in (
            ("attorney-general", "2026-11-03:GU:66:attorney-general"),
            ("public-auditor", "2020-11-03:GU:public-auditor"),
        ):
            (offices[what],) = one(
                conn,
                """select c.office_id from contest c join external_id x on x.entity_id = c.id
                   where x.scheme = 'us.contest' and x.value = %s""",
                [value],
            )
        for court, (key, title, _) in COURTS.items():
            office = cs.key("us.office", key)
            offices[court] = office
            if office in cs.minted:
                cs.add("office", id=office, polity_id=state, kind="other")
                name(office, title)
        for key, (_, polity, title) in places.items():
            for role, kind, label in (("mayor", "mayor", "Mayor"), ("vice-mayor", "deputy_mayor", "Vice Mayor")):
                office = cs.key("us.office", f"{role}:{villages[key][0]}")
                offices[f"{role}:{key}"] = office
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=polity, kind=kind)
                    name(office, f"{label} of {title}")
        systems = {
            name_: cs.key("us.electoral_system", name_) for name_ in ("fptp", "block", "retention", "referendum")
        }
        parties = {code: cs.key("us.party", value) for code, value in PARTIES.items()}
        held = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join contest c on c.id = x.entity_id
                   join changeset s on s.id = c.changeset_id
                   where x.scheme = 'us.contest' and x.value like '%%:GU:%%' and s.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        known: dict[str, list[tuple[str, UUID]]] = {}
        for person, text in conn.execute(
            """select distinct n.entity_id, n.text from candidacy y join contest_option o on o.id = y.option_id
               join contest c on c.id = o.contest_id join election e on e.id = c.election_id
               join name n on n.entity_id = y.person_id
               where e.polity_id = %s""",
            [state],
        ).fetchall():
            known.setdefault(fold(text), []).append((text, person))
        persons: dict[str, UUID] = {}

        def person_for(display: str, key: str) -> UUID:
            if key in persons:
                return persons[key]
            surname, first = key.split(":")
            matches = {
                person
                for folded, rows in known.items()
                if folded.startswith(first) and surname in folded[len(first) :]
                for _, person in rows
            }
            if len(matches) == 1:
                persons[key] = matches.pop()
            else:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key])
                    name(persons[key], display)
            return persons[key]

        sources: dict[date, UUID] = {}
        made = 0
        for (day, heading), entry in sorted(ballots.items()):
            kind = classify(heading)
            if kind is None:
                continue
            what, detail = kind
            units: dict[str, dict[str, int]] = entry["units"]  # ty: ignore[invalid-assignment]
            vote_for = int(entry["vote_for"] or 1)  # ty: ignore[invalid-argument-type]
            year = day.year
            path = f"us/gu/{day}-general"
            election = cs.key("us.election", f"{day}:GU")
            body = office = None
            magnitude = vote_for
            if what == "body":
                title, short = BODIES[detail]
                body = bodies[detail]
                key = f"{day}:GU:{short}"
                contest_name, contest_slug = f"{year} {title} election", short
            elif what in ("attorney-general", "public-auditor"):
                office = offices[what]
                key = f"{day}:GU:{what}"
                label = "Attorney General" if what == "attorney-general" else "Public Auditor"
                contest_name, contest_slug = f"{year} Guam {label} election", what
            elif what in ("mayor", "vice-mayor"):
                office = offices[f"{what}:{detail}"]
                title = places[detail][2]
                key = f"{day}:GU:{what}:{slug(title)}"
                label = "mayor" if what == "mayor" else "vice mayor"
                contest_name, contest_slug = f"{year} {title} {label} election", f"{what}-{slug(title)}"
            elif what == "retention":
                judge, court = JUDGES[detail]
                office = offices[court]
                key = f"{day}:GU:retention:{slug(judge)}"
                contest_name = f"{year} Guam {COURTS[court][2]} retention: {judge}"
                contest_slug = f"retention-{slug(judge)}"
            else:
                key = f"{day}:GU:referendum:{REFERENDUM[1]}"
                contest_name, contest_slug = REFERENDUM
            if key in held:
                continue
            district = places[detail][0] if what in ("mayor", "vice-mayor") else territory
            if what in ("mayor", "vice-mayor"):
                units = {code: votes for code, votes in units.items() if any(votes.values())}
            contest = cs.key("us.contest", key)
            question = what in ("retention", "referendum")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question" if what == "referendum" else "body_seats" if body else "office",
                office_id=office,
                body_id=body,
                district_area_id=district,
                term=None if what == "referendum" else "full",
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems[
                    "referendum"
                    if what == "referendum"
                    else "retention"
                    if what == "retention"
                    else "block"
                    if magnitude > 1
                    else "fptp"
                ],
                default_result_kind="counted",
            )
            name(contest, contest_name)
            if contest in cs.minted:
                cs.add("slug", path=f"{path}/{contest_slug}", entity_id=contest)
            if what == "referendum":
                cs.add("ballot_question", contest_id=contest, type="optional_referendum")
            if day not in sources:
                title = f"{year} General Election official results by precinct"
                sources[day] = cs.source(PUBLISHER, title)
                if sources[day] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[day],
                        kind="official_body",
                        publisher=PUBLISHER,
                        title=title,
                        url="https://gec.guam.gov/",
                        license="Public record",
                    )
                for artifact in artifacts[day]:
                    cs.artifact(artifact, sources[day])
            cs.add("citation", entity_id=contest, source_id=sources[day])
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[day],
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md="Votes by precinct from the Guam Election Commission's official precinct reports; over and under votes are left out.",
            )
            options: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            labels = list(dict.fromkeys(label for votes in units.values() for label in votes))
            for position, label in enumerate(labels, start=1):
                if question:
                    answer = label.strip().lower()
                    option = cs.key("us.option", f"{key}:{answer}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position,
                        ballot_label=answer.capitalize(),
                    )
                    if what == "retention" and answer == "yes":
                        judge = JUDGES[detail][0]
                        surname = fold(judge.replace(" Jr.", "").replace(" III", "").split()[-1])
                        given = fold(judge.split()[0])
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:yes:head"),
                            person_id=person_for(
                                judge,
                                f"{surname}:{given}",
                            ),
                            option_id=option,
                            role="head",
                            ballot_name=heading,
                            is_independent=False,
                        )
                elif re.match(r"(?i)write-?in", label):
                    option = cs.key("us.option", f"{key}:write-in")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-in")
                else:
                    display, person_key, party, incumbent = person_name(label)
                    option = cs.key("us.option", f"{key}:{person_key}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=position,
                        ballot_label=display,
                        party_id=parties.get(party or ""),
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person_key}:head"),
                        person_id=person_for(display, person_key),
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=False,
                        is_incumbent=True if incumbent else None,
                    )
                options[label] = option
                totals[option] = sum(votes.get(label, 0) for votes in units.values())
            if question:
                yes = next(option for label, option in options.items() if label.strip().lower() == "yes")
                no = next(option for label, option in options.items() if label.strip().lower() == "no")
                passed = totals[yes] > totals[no]
                if what == "retention":
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=yes,
                        kind="elected" if passed else "lost",
                        seats=1 if passed else None,
                    )
                else:
                    cs.add("outcome", result_set_id=result_set, option_id=yes, kind="won" if passed else "lost")
                    cs.add("outcome", result_set_id=result_set, option_id=no, kind="lost" if passed else "won")
            else:
                ranked = sorted(
                    (option for label, option in options.items() if not re.match(r"(?i)write-?in", label)),
                    key=lambda option: -totals[option],
                )
                if len(ranked) > magnitude and totals[ranked[magnitude - 1]] == totals[ranked[magnitude]]:
                    raise ValueError(f"{key}: tie at the last seat")
                for option in ranked[:magnitude]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
            for code, votes in units.items():
                unit = cs.unit("us.precinct", code)
                if unit in cs.minted and not any(r["id"] == unit for r in cs.rows.get("reporting_unit", [])):
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=state,
                        scheme_code="us.precinct",
                        code=code,
                        parent_id=parent,
                        area_relation="within",
                        area_id=territory,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                for label, count in votes.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=options[label],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=count,
                    )
            made += 1
            print(f"{key}: {magnitude} of {len(options)}, {len(units)} precincts, {sum(totals.values())} votes")
        print(made, "contests")
        return cs.write()
