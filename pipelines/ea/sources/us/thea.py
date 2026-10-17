import difflib
import json
import re
import zipfile
from collections import Counter
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl
from psycopg.types.range import Range

from ea import names, raw
from ea.changeset import Changeset, slug
from ea.sources import thea
from ea.sources.us.clerk import LEGISLATORS, STATES, plain
from ea.store import DATA, connect

SOURCE = "us.thea"
VERSION = "3"
APART = 24 * 365
PARSED = DATA / "parsed" / SOURCE
DERIVED = 1
LOWER = f"{thea.UPLOADS}/2025/10/clea_lc_20251015-L-Z.xlsx"
UPPER = f"{thea.UPLOADS}/2026/09/clea_uc_20260908_excel.zip"
UPPER_TITLE = "Constituency-Level Elections Archive, upper chamber (2026-09-08)"
UPPER_LANDING = "https://electiondataarchive.org/data-and-documentation/clea-upper-chamber-elections-archive/"
CONFEDERATE = {"VA", "NC", "SC", "GA", "FL", "AL", "MS", "LA", "TX", "AR", "TN"}
NATIONAL = {
    180: "democratic-party",
    583: "republican-party",
    739: "whig-party",
    218: "federalist-party",
    186: "democratic-republican-party",
    70: "anti-masonic-party",
    31: "know-nothing-party",
    405: "liberal-republican-party",
    552: "prohibition-party",
    529: "peoples-party",
    543: "progressive-party-1912",
    447: "progressive-party-1912",
    599: "progressive-party-1912",
    616: "socialist-party-of-america",
    619: "socialist-labor-party-of-america",
    253: "greenback-party",
    232: "free-soil-party",
    410: "liberty-party",
}
ERAS = {
    "national-republican-party": (1824, 1834),
    "peoples-party": (1891, 1908),
    "progressive-party-1912": (1912, 1918),
    "socialist-party-of-america": (1901, 1918),
    "liberty-party": (1840, 1848),
    "free-soil-party": (1848, 1854),
    "greenback-party": (1874, 1888),
}
NAMES = {
    "socialist-party-of-america": "Socialist Party of America",
    "socialist-labor-party-of-america": "Socialist Labor Party of America",
    "greenback-party": "Greenback Party",
    "free-soil-party": "Free Soil Party",
    "liberty-party": "Liberty Party",
}
UPPER_PARTIES = {
    "Democrat": "democratic-party",
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Progressive": "progressive-party-1912",
    "Prohibition": "prohibition-party",
    "Socialist": "socialist-party-of-america",
    "Socialist Labor": "socialist-labor-party-of-america",
}
ROSTER_NOTE = (
    "THEA's archive marks no winner in this race. The winner shown is the member who took the seat according to the "
    "congress-legislators roster of members of Congress."
)
MONTH_NOTE = "THEA's archive only contains the month for this election; the date shown is the first of that month."
YEAR_NOTE = "THEA's archive only contains the year for this election; the date shown is January 1 of that year."
REWORDED = {
    ROSTER_NOTE: (
        "THEA's archive marks no winner in this race. The winner shown is the member who took the seat, from the "
        "congress-legislators roster."
    ),
    MONTH_NOTE: "THEA's archive gives only the month of this election, so the date shown is the first of that month.",
    YEAR_NOTE: "THEA's archive gives only the year of this election, so the date shown is January 1 of that year.",
}


def congress(day: date) -> int:
    return (day.year - 1789 - ((day.month, day.day) < (3, 4))) // 2 + 1


def name(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("(", " ").replace(")", " ")).strip(" ,")
    last, _, first = text.partition(",")
    words = f"{first.strip()} {last.strip()}".strip().split(" ")
    fixed = []
    for word in words:
        word = word.capitalize()
        word = re.sub(r"^(Mc|O')([a-z])", lambda m: m[1] + m[2].upper(), word)
        word = re.sub(r"(?i)^(jr|sr)\.?$", lambda m: m[1].capitalize() + ".", word)
        fixed.append(word.upper() if re.fullmatch(r"(?i)[ivx]+|[a-z]", word) and word.lower() != "i" else word)
    return " ".join(fixed)


def parse() -> None:
    lower = (
        pl.read_parquet(DATA / "parsed" / thea.SOURCE / "lower.parquet")
        .filter((pl.col("ctr") == 840) & (pl.col("yr") <= 1918))
        .with_columns(
            chamber=pl.lit("house"),
            state=pl.col("sub").str.to_uppercase().replace_strict(STATES),
            district=pl.col("cst_n").str.extract(r"(\d+)$").cast(pl.Int64).fill_null(0),
        )
    )
    with zipfile.ZipFile(raw.cached(thea.SOURCE, UPPER).path) as archive:
        workbook = next(member for member in archive.namelist() if member.endswith(".xlsx"))
        upper = fastexcel.read_excel(archive.read(workbook)).load_sheet(0).to_polars()
    upper = upper.filter((pl.col("ctr") == 840) & (pl.col("yr") >= 1913) & (pl.col("yr") <= 1918)).with_columns(
        chamber=pl.lit("senate"),
        state=pl.col("cst_n").str.to_uppercase().str.strip_chars().replace_strict(STATES),
        district=pl.lit(None, pl.Int64),
    )
    columns = ["chamber", "yr", "mn", "state", "district", "cst", "can", "pty", "pty_n", "cv1", "vv1", "seat"]
    frame = pl.concat(
        [
            part.select(
                "chamber",
                pl.col("yr").cast(pl.Int64),
                pl.col("mn").cast(pl.Int64),
                "state",
                "district",
                pl.col("cst").cast(pl.Int64),
                pl.col("can").cast(pl.String).str.strip_chars(),
                pl.col("pty").cast(pl.Int64),
                pl.col("pty_n").cast(pl.String).str.to_lowercase().str.strip_chars(),
                pl.col("cv1").cast(pl.Float64),
                pl.col("vv1").cast(pl.Float64),
                pl.col("seat").cast(pl.Float64),
            )
            for part in (lower, upper)
        ]
    ).select(columns)

    roster: dict[tuple[str, int, str], list[dict]] = {}
    for kind in ("historical", "current"):
        for person in json.loads(raw.cached("us.clerk", LEGISLATORS.format(kind=kind)).path.read_text()):
            for term in person["terms"]:
                began = date.fromisoformat(term["start"])
                chamber = "senate" if term["type"] == "sen" else "house"
                full = person["name"].get("official_full") or " ".join(
                    part
                    for part in (person["name"]["first"], person["name"].get("middle"), person["name"]["last"])
                    if part
                )
                if person["name"].get("suffix"):
                    full = f"{full}, {person['name']['suffix']}" if "," not in full else full
                for number in range(congress(began), congress(date.fromisoformat(term["end"]) - timedelta(days=1)) + 1):
                    roster.setdefault((chamber, number, term["state"]), []).append(
                        {
                            "last": plain(person["name"]["last"]),
                            "full": full,
                            "ids": person["id"],
                            "district": None if chamber == "senate" else max(int(term.get("district", 0)), 0),
                            "class": term.get("class"),
                            "start": began,
                            "first_congress": congress(began) == number,
                        }
                    )
    starts: dict[int, Counter[date]] = {}
    for (chamber, number, _), members in roster.items():
        for member in members:
            if chamber == "house" and member["first_congress"]:
                starts.setdefault(number, Counter())[member["start"]] += 1
    sessions = {number: counter.most_common(1)[0][0] for number, counter in starts.items()}

    def matches(can: str, chamber: str, number: int, state: str, district: int | None) -> list[dict]:
        last = plain(can.split(",")[0])
        if len(last) < 2:
            return []
        pool = [
            member
            for member in roster.get((chamber, number, state), [])
            if member["first_congress"] or chamber == "senate"
        ]
        found = [member for member in pool if member["last"] == last]
        if not found:
            found = [
                member
                for member in pool
                if difflib.SequenceMatcher(None, member["last"], last).ratio() >= 0.8
                or (len(last) >= 4 and (member["last"].startswith(last) or last.startswith(member["last"])))
            ]
        if len(found) > 1 and district is not None:
            found = [member for member in found if member["district"] == district] or found
        if len(found) > 1:
            first = plain(can.partition(",")[2])[:1]
            found = [member for member in found if plain(member["full"]).startswith(first)] or found
        found = list({member["ids"]["bioguide"]: member for member in found}.values())
        return found if len(found) == 1 else []

    rows: list[dict] = []
    problems: list[str] = []
    for (chamber, year, month, state, cst), group in frame.group_by(
        "chamber", "yr", "mn", "state", "cst", maintain_order=True
    ):
        lines = group.rows(named=True)
        district = lines[0]["district"]
        if month == 11 and year % 2 == 0 and year >= 1876:
            day, precision = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7), "day"
        elif month == 9 and state == "ME":
            day, precision = date(year, 9, 1) + timedelta(days=(-date(year, 9, 1).weekday()) % 7 + 7), "day"
        elif 1 <= month <= 12:
            day, precision = date(year, month, 1), "month"
        else:
            day, precision = date(year, 1, 1), "year"
        if year % 2 == 1 and day <= date(year, 3, 4):
            order = [congress(date(year, 3, 4)), congress(date(year, 3, 4)) - 1]
        elif year % 2 == 1:
            order = [congress(day), congress(day) + 1]
        else:
            order = [congress(day) + 1, congress(day)]
        declared = [line for line in lines if line["seat"] in (1.0, -992.0)]
        candidates = [line for line in lines if line["can"] not in ("-990", "-992", "")]
        tried = {
            number: {
                index: hit[0]
                for index, line in enumerate(lines)
                if line in (declared or candidates) and (hit := matches(line["can"], chamber, number, state, district))
            }
            for number in order
        }
        midterm = [
            number
            for number, hits in tried.items()
            if chamber == "house"
            and hits
            and all(
                member["start"] > sessions[number] + timedelta(days=45) and member["start"] >= day - timedelta(days=31)
                for member in hits.values()
            )
        ]
        chosen = next(iter(midterm), next((number for number in order if tried[number]), order[0]))
        found = tried[chosen]
        if state in CONFEDERATE and date(1861, 2, 1) <= day < date(1865, 6, 1) and not found and chamber == "house":
            problems.append(f"confederate {year}-{month} {state}-{district}: {[line['can'] for line in declared]}")
            continue
        roster_winner = not declared and bool(found)
        winners = {index for index, line in enumerate(lines) if line in declared} or (
            set(found) if roster_winner else set()
        )
        if not winners:
            problems.append(f"no winner {chamber} {year}-{month} {state}-{district}")
        members = [found[index] for index in winners if index in found]
        if (
            chamber == "house"
            and len(winners) == 1
            and members
            and district
            and members[0]["district"]
            and members[0]["district"] != district
        ):
            problems.append(f"district {year} {state}: THEA {district}, roster {members[0]['district']}")
            district = members[0]["district"]
        seat_class = next((member["class"] for member in members if member["class"] is not None), None)
        if chamber == "senate" and seat_class is None:
            problems.append(f"no class {year} {state} senate: {[line['can'] for line in declared]}")
            continue
        if chamber == "senate":
            special = seat_class is not None and seat_class != (3, 1, 2)[(year - 2004) % 6 // 2]
        else:
            special = any(member["start"] > sessions[chosen] + timedelta(days=45) for member in members)
        for index, line in enumerate(lines):
            member = found.get(index) if index in winners else None
            party = None
            independent = False
            if line["can"] in ("-990", "-992", ""):
                kind = "others"
            else:
                kind = "candidate"
                code = line["pty"]
                label = re.sub(r"\s*\(.*\)$", "", line["pty_n"] or "").strip()
                if chamber == "senate":
                    party = UPPER_PARTIES.get(label.title())
                    independent = label in ("independent", "-990") and party is None
                    if party is None and not independent:
                        party = f"{state}:{slug(label.title())}"
                elif code in NATIONAL and (
                    NATIONAL[code] not in ERAS or ERAS[NATIONAL[code]][0] <= year <= ERAS[NATIONAL[code]][1]
                ):
                    party = NATIONAL[code]
                elif code >= 6000 or label == "independent":
                    independent = True
                elif code not in (3999, 4000) and label:
                    party = f"{state}:{slug(label.title())}"
            rows.append(
                {
                    "chamber": chamber,
                    "year": year,
                    "month": month,
                    "day": day,
                    "precision": precision,
                    "state": state,
                    "district": district,
                    "printed_district": lines[0]["district"],
                    "cst": cst,
                    "congress": chosen,
                    "special": special,
                    "seat_class": seat_class,
                    "magnitude": max(
                        len({found[i]["ids"]["bioguide"] if i in found else plain(lines[i]["can"]) for i in winners}), 1
                    ),
                    "line": index,
                    "kind": kind,
                    "name": name(line["can"]) if kind == "candidate" else None,
                    "party": party,
                    "party_label": None
                    if party is None
                    else re.sub(r"(^|[\s\-/,(])([a-z])", lambda m: m[1] + m[2].upper(), line["pty_n"] or ""),
                    "independent": independent,
                    "votes": int(line["cv1"]) if line["cv1"] is not None and line["cv1"] >= 0 else None,
                    "uncontested": line["seat"] == -992.0 or line["cv1"] == -992.0,
                    "elected": index in winners,
                    "roster_winner": roster_winner,
                    "member": None if member is None else json.dumps(member["ids"], sort_keys=True),
                    "member_name": None if member is None else member["full"],
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    out = pl.DataFrame(rows, infer_schema_length=None)
    contests = out.select("chamber", "day", "state", "special", "cst", "district", "printed_district").unique()
    final: dict[tuple, int] = {}
    for _, group in contests.group_by("chamber", "day", "state", "special"):
        taken = set(group["printed_district"])
        for chamber, day, state, special, cst, district, printed in group.rows():
            if district != printed and district not in taken:
                taken.add(district)
                final[(chamber, day, state, special, cst)] = district
            else:
                final[(chamber, day, state, special, cst)] = printed
    out = out.with_columns(
        district=pl.struct("chamber", "day", "state", "special", "cst").map_elements(
            lambda row: final[tuple(row.values())], return_dtype=pl.Int64
        )
    )
    keys = ["chamber", "day", "state", "district", "special"]
    if out.select(pl.col("cst").n_unique().over(keys) > 1).to_series().any():
        raise ValueError("two contests share a district")
    out.write_parquet(PARSED / "contests.parquet")
    (PARSED / "problems.txt").write_text("\n".join(problems))
    contests = out.select("chamber", "day", "state", "district", "cst").unique().height
    winners = out.filter("elected")
    print(
        f"{contests} contests, {winners.height} winners, {winners.filter(pl.col('member').is_null()).height} not on the "
        f"roster, {len(problems)} problems; see {PARSED / 'problems.txt'}"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "contests.parquet")
    with connect() as conn:
        states: dict[str, tuple[UUID, UUID, str, str]] = {
            iso.removeprefix("US-"): (unit, area, text, code)
            for text, iso, unit, area, code in conn.execute(
                """select n.text, p.iso_code, u.id, u.area_id, u.code from reporting_unit u
                   join polity p on p.id = u.polity_id
                   join name n on n.entity_id = p.id and n.is_preferred
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        districts: dict[str, list[tuple[UUID, UUID, str, Range[date]]]] = {}
        for code, area, unit, text, valid in conn.execute(
            """select u.code, u.area_id, u.id, n.text, u.valid from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'official'
               where u.scheme_code = 'us.cd'"""
        ).fetchall():
            districts.setdefault(code, []).append((area, unit, text, valid))
        plans: list[tuple[UUID, date, date | None]] = conn.execute(
            "select id, valid_from, valid_to from boundary_set where scheme_code = 'us.cd'"
        ).fetchall()
        known: dict[str, dict[str, UUID]] = {}
        for scheme, value, entity in conn.execute(
            """select scheme, value, entity_id from external_id
               where scheme in ('fec.candidate', 'wikidata', 'bioguide')"""
        ).fetchall():
            known.setdefault(scheme, {})[value] = entity
        identified = {(scheme, entity) for scheme, values in known.items() for entity in values.values()}
        notes: dict[tuple[UUID, str], UUID] = {
            (entity, body): id
            for id, entity, body in conn.execute(
                """select n.id, n.entity_id, n.body_md from note n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House general results, 1788–1918, and Senate popular elections, 1914–1918, from THEA",
            polity="US",
        )

        def named(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        bodies = {kind: cs.key("us.body", kind) for kind in ("house", "senate")}
        systems = {kind: cs.key("us.electoral_system", kind) for kind in ("fptp", "block")}
        if cs.minted:
            raise ValueError("the House, the Senate, and their electoral systems must already exist")

        sources = {}
        for chamber, title, landing, url in (
            ("house", thea.TITLE, thea.LANDING, LOWER),
            ("senate", UPPER_TITLE, UPPER_LANDING, UPPER),
        ):
            sources[chamber] = cs.source(thea.PUBLISHER, title)
            cs.add(
                "source",
                id=sources[chamber],
                kind="academic",
                publisher=thea.PUBLISHER,
                title=title,
                url=landing,
                license=thea.LICENSE,
                license_url=thea.LICENSE_URL,
                attribution=thea.ATTRIBUTION,
                notes=thea.NOTES,
            )
            cs.artifact(raw.cached(thea.SOURCE, url), sources[chamber])

        elections: dict[str, UUID] = {}

        def election(
            key: str,
            polity: UUID,
            kind: str,
            span: tuple[date, date],
            precision: str,
            title: str,
            path: str,
            parent: UUID | None = None,
        ) -> UUID:
            if key not in elections:
                elections[key] = cs.key("us.election", key)
                cs.add(
                    "election",
                    id=elections[key],
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=span[0],
                    date_to=span[1],
                    date_precision=precision,
                    status="held",
                )
                named(elections[key], title)
                cs.add("slug", path=path, entity_id=elections[key])
            return elections[key]

        parties: dict[str, UUID] = {}
        for key, label in frame.select("party", "party_label").drop_nulls().unique("party").sort("party").iter_rows():
            state, _, code = key.rpartition(":")
            parties[key] = cs.key("us.party", key)
            if parties[key] in cs.minted:
                cs.add("party", id=parties[key], kind="party", polity_id=cs.polity(f"US-{state}") if state else us)
                named(parties[key], NAMES.get(key) or re.sub(r"\s*\(.*\)$", "", label).strip())

        persons: dict[str, UUID] = {}
        created: set[UUID] = set()
        for member, full in (
            frame.filter(pl.col("member").is_not_null()).select("member", "member_name").unique("member").iter_rows()
        ):
            ids = json.loads(member)
            person = next(
                (known["fec.candidate"][fec] for fec in ids.get("fec", []) if fec in known["fec.candidate"]), None
            )
            person = (
                person
                or known.get("wikidata", {}).get(ids.get("wikidata", ""))
                or known.get("bioguide", {}).get(ids["bioguide"])
            )
            if person is None:
                person = (
                    cs.key("wikidata", ids["wikidata"]) if "wikidata" in ids else cs.key("bioguide", ids["bioguide"])
                )
            if person in cs.minted and person not in created:
                created.add(person)
                cs.add("person", id=person)
                named(person, full)
            for scheme, value in (("wikidata", ids.get("wikidata")), ("bioguide", ids["bioguide"])):
                if (
                    value is not None
                    and value not in known.get(scheme, {})
                    and (scheme, person) not in identified
                    and (scheme, value) not in cs._keyed
                ):
                    cs.add("external_id", entity_id=person, scheme=scheme, value=value)
                    identified.add((scheme, person))
            persons[member] = person

        runs: dict[tuple[str, str], dict[int | None, list[date]]] = {}
        for state, text, district, day in (
            frame.filter(pl.col("member").is_null() & (pl.col("kind") == "candidate"))
            .select("state", "name", "district", "day")
            .unique()
            .sort("day")
            .iter_rows()
        ):
            handle = slug(str(text))
            runs.setdefault((state, handle), {}).setdefault(None if "-" in handle else district, []).append(day)
        labeled: dict[UUID, UUID] = dict(
            conn.execute(
                """select o.id, o.party_id from contest_option o join changeset c on c.id = o.changeset_id
                   where c.pipeline = 'us.party_labels'"""
            ).fetchall()
        )
        held: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = %s", [f"{SOURCE}.person"]).fetchall()
        )
        eras: dict[tuple[str, str, int | None, date], str] = {}
        for (state, handle), places in runs.items():
            segments: list[tuple[int | None, list[date]]] = []
            for district, days in places.items():
                segments.append((district, [days[0]]))
                for earlier, later in pairwise(days):
                    if (later - earlier).days > APART:
                        segments.append((district, []))
                    segments[-1][1].append(later)
            segments.sort(key=lambda segment: segment[1][0])
            original = f"{state}:{handle}"
            for index, (district, days) in enumerate(segments):
                key = original
                if index and str(held.get(original)) not in cs.merged:
                    place = "" if district is None else f"{district}:"
                    key = f"{original}:{place}{days[0].year}"
                for day in days:
                    eras[state, handle, district, day] = key

        def person_of(row: dict) -> UUID:
            if row["member"] is not None:
                return persons[row["member"]]
            handle = slug(str(row["name"]))
            key = eras[row["state"], handle, None if "-" in handle else row["district"], row["day"]]
            person = cs.key(f"{SOURCE}.person", key)
            if person in cs.minted and (old := held.get(f"{row['state']}:{handle}")) is not None:
                cs.renamed[str(person)] = str(old)
            if person in cs.minted and person not in created:
                created.add(person)
                cs.add("person", id=person)
                named(person, str(row["name"]))
            return person

        spans: dict[int, tuple[date, date]] = {}
        for number, first, last in (
            frame.group_by("congress")
            .agg(pl.col("day").min().alias("first"), pl.col("day").max().alias("last"))
            .iter_rows()
        ):
            spans[number] = (first, last)
        contests: set[str] = set()
        folds: dict[UUID, list[UUID]] = {}
        for (chamber, day, state, district, cst), group in frame.group_by(
            "chamber", "day", "state", "district", "cst", maintain_order=True
        ):
            rows = group.sort("line").rows(named=True)
            first = rows[0]
            number, precision, special, year = first["congress"], first["precision"], first["special"], day.year
            unit, area, place, fips = states[state]
            polity = cs.polity(f"US-{state}")
            start = date(1789 + 2 * (number - 1), 3, 4)
            if year >= 1876 and year % 2 == 0:
                general = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)
                umbrella = election(
                    str(general),
                    us,
                    "general",
                    (general, general),
                    "day",
                    f"{year} United States general election",
                    f"us/{general}-general",
                )
            else:
                low, high = start.year - 1, start.year
                umbrella = election(
                    f"congress-{number}",
                    us,
                    "general",
                    spans[number],
                    "month",
                    f"{low}–{high % 100:02d} United States House of Representatives elections",
                    f"us/{low}-{high % 100:02d}-house-elections",
                )
            stamp = str(day) if precision == "day" else f"{day:%Y-%m}" if precision == "month" else f"{year}"
            parent = election(
                f"{stamp}:{state}",
                polity,
                "general",
                (day, day),
                precision,
                f"{year} {place} general election"
                if precision == "day" and day.month == 11
                else f"{day:%B %Y} {place} general election"
                if precision != "year"
                else f"{year} {place} general election",
                f"us/{state.lower()}/{stamp}-general",
                umbrella,
            )
            on = day if special else start
            plan = next((id for id, begin, end in plans if begin <= on and (end is None or on < end)), None)
            if chamber == "senate":
                number_class = first["seat_class"]
                seat: UUID | None = cs.key("us.seat", f"senate:{state}:{number_class}")
                if seat in cs.minted:
                    raise ValueError(f"{year} {state}: no Senate class {number_class} seat")
                part = "senate"
                contest_key = f"{stamp}:{state}:senate"
                title_place = place
            else:
                matches = [entry for entry in districts.get(f"{fips}{district:02d}", []) if on in entry[3]]
                if len(matches) == 1:
                    area, unit, title_place, _ = matches[0]
                else:
                    title_place = f"{place} at large" if district == 0 else f"{place}'s district {district}"
                seat = None
                part = f"house-{district}" if district else "house-at-large"
                contest_key = f"{stamp}:{state}:house:{district}"
            contest_key += ":special" if special else ""
            if contest_key in contests:
                raise ValueError(f"{contest_key}: two contests")
            contests.add(contest_key)
            magnitude = int(first["magnitude"])
            tacit = all(row["votes"] is None for row in rows) and all(row["uncontested"] for row in rows)
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=parent,
                kind="body_seats",
                body_id=bodies[chamber],
                district_area_id=area,
                seat_id=seat,
                term="remainder" if special else "full",
                boundary_set_id=None if chamber == "senate" else plan,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["block" if magnitude > 1 else "fptp"],
                default_result_kind="tacit" if tacit else "counted",
            )
            named(
                contest,
                f"{year} U.S. {'Senate' if chamber == 'senate' else 'House'} "
                f"{'special election' if special else 'election'} in {title_place}",
            )
            cs.add(
                "slug",
                path=f"us/{state.lower()}/{stamp}-general/{part}{'-special' if special else ''}",
                entity_id=contest,
            )
            values: list[tuple[UUID | None, int]] = []
            winners: list[UUID] = []
            people: dict[UUID, list[dict]] = {}
            for row in rows:
                if row["kind"] == "others":
                    option = cs.key("us.option", f"{contest_key}:others:{row['line']}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Others")
                    if row["votes"] is not None:
                        values.append((option, row["votes"]))
                else:
                    people.setdefault(person_of(row), []).append(row)
            found = list(people)
            labels = {person: lines[0]["member_name"] or lines[0]["name"] for person, lines in people.items()}
            before = {person: list(lines) for person, lines in people.items()}
            folded = names.fold(
                [people[person][0]["name"] for person in found],
                [people[person][0]["member"] is not None for person in found],
            )
            duplicates: dict[UUID, UUID] = {}
            for index, survivor in folded.items():
                duplicates[found[index]] = found[survivor]
                people[found[survivor]] += people.pop(found[index])
            for lines in people.values():
                lines.sort(key=lambda row: row["line"])
            counted = [row["votes"] for row in rows if row["votes"] is not None]
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            previous = None
            if duplicates and result_set not in cs.minted:
                previous = result_set
                cs.add(
                    "result_set",
                    id=previous,
                    election_id=parent,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="superseded",
                    source_id=sources[chamber],
                    is_default=False,
                    completeness=None if tacit else 1 if len(counted) == len(rows) else 0,
                )
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:folded")
            cs.add(
                "result_set",
                id=result_set,
                election_id=parent,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=sources[chamber],
                is_default=True,
                supersedes_id=previous,
                completeness=None if tacit else 1 if len(counted) == len(rows) else 0,
            )
            for applies, kind, body in (
                (first["roster_winner"], "caveat", ROSTER_NOTE),
                (precision == "month", "caveat", MONTH_NOTE),
                (precision == "year", "caveat", YEAR_NOTE),
            ):
                if applies:
                    id = notes.get((result_set, body)) or notes.get((result_set, REWORDED[body]))
                    cs.add("note", **({"id": id} if id else {}), entity_id=result_set, kind=kind, body_md=body)
            for person, lines in people.items():
                option_key = f"{contest_key}:{person}"
                root = cs.key("us.option", option_key)
                label = labels[person]
                nominated = list(dict.fromkeys(parties[row["party"]] for row in lines if row["party"] is not None))
                cs.add(
                    "contest_option",
                    id=root,
                    contest_id=contest,
                    kind="candidate",
                    party_id=labeled.get(root, nominated[0] if nominated else None) if len(lines) == 1 else None,
                    ballot_label=label,
                )
                head = cs.key("us.candidacy", f"{option_key}:head")
                cs.add(
                    "candidacy",
                    id=head,
                    person_id=person,
                    option_id=root,
                    role="head",
                    ballot_name=label,
                    is_independent=not nominated and any(row["independent"] for row in lines),
                    is_incumbent=None,
                )
                for party in nominated:
                    cs.add("candidacy_nomination", candidacy_id=head, party_id=party, kind="nominated")
                if any(row["elected"] for row in lines):
                    winners.append(root)
                for row in lines:
                    option = root
                    if len(lines) > 1:
                        line_label = f"{label} ({row['party_label'] or 'no party'})"
                        option = cs.key("us.option", f"{option_key}:{slug(line_label)}:{row['line']}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            parent_option_id=root,
                            kind="candidate",
                            party_id=parties.get(row["party"]) if row["party"] is not None else None,
                            ballot_label=line_label,
                        )
                    if row["votes"] is not None:
                        values.append((option, row["votes"]))
            if counted:
                values.append((None, sum(counted)))
            if result_set in cs.minted:
                for option, votes in values:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1 if option else 4,
                        count_round=0,
                        flags=0 if option else DERIVED,
                        value=votes,
                    )
            for option in winners:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            for duplicate, person in duplicates.items():
                kept, gone = f"{contest_key}:{person}", f"{contest_key}:{duplicate}"
                old = cs.key("us.option", gone)
                if old in cs.minted:
                    continue
                lines = before[duplicate]
                for row in lines:
                    line = f"{labels[person]} ({row['party_label'] or 'no party'})"
                    into = cs.key("us.option", f"{kept}:{slug(line)}:{row['line']}")
                    if len(lines) == 1:
                        cs.merge(old, into)
                    else:
                        line = f"{labels[duplicate]} ({row['party_label'] or 'no party'})"
                        cs.merge(cs.key("us.option", f"{gone}:{slug(line)}:{row['line']}"), into)
                if len(lines) > 1:
                    cs.merge(old, cs.key("us.option", kept))
                cs.merge(cs.key("us.candidacy", f"{gone}:head"), cs.key("us.candidacy", f"{kept}:head"))
                folds.setdefault(duplicate, []).append(person)
            if previous is not None:
                for option, kind, seats in conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [previous]
                ).fetchall():
                    cs.add(
                        "outcome",
                        result_set_id=previous,
                        option_id=cs.survivors.get(option, option),
                        kind=kind,
                        seats=seats,
                    )
        candidacies: dict[UUID, int] = dict(
            conn.execute(
                "select person_id, count(*) from candidacy where person_id = any(%s) group by 1", [list(folds)]
            ).fetchall()
        )
        for duplicate, into in folds.items():
            if len(set(into)) == 1 and candidacies.get(duplicate, 0) == len(into) and duplicate not in cs.minted:
                cs.merge(duplicate, into[0])
        return cs.write()
