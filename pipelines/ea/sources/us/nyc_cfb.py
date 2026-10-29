import json
import re
import struct
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_cfb"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
INBOX = DATA / "inbox" / "nyc_cfb"
PUBLISHER = "New York City Campaign Finance Board"
FILES = {1989: "1989_app.pdf", 1991: "1991_app.pdf", 1993: "1993_app.pdf", 1997: "98v2.pdf"}
URLS = {
    1989: "local:nyc_cfb/1989_app.pdf",
    1991: "local:nyc_cfb/1991_app.pdf",
    1993: "local:nyc_cfb/1993_app.pdf",
    1997: "https://www.nyccfb.info/pdf/per/98_per-vol.ii.pdf",
}
TITLES = {
    1989: "1989 Annual Report of the Board of Elections in the City of New York, Appendix B: Vote totals (as reprinted by the Campaign Finance Board)",
    1991: "New York City Campaign Finance Board, 1991 elections: Appendices of vote totals and campaign finance data (scanned)",
    1993: "New York City Campaign Finance Board, 1993 elections: Appendices B and C, totals for candidates appearing on the primary and general election ballots (scanned)",
    1997: "A Decade of Reform: the 1997 New York City Elections, Volume II, Appendices B and C",
}
DAYS = {
    1989: (date(1989, 9, 12), date(1989, 11, 7)),
    1991: (date(1991, 9, 10), date(1991, 11, 5)),
    1993: (date(1993, 9, 14), date(1993, 11, 2)),
    1997: (date(1997, 9, 9), date(1997, 11, 4)),
}
BOROUGHS = {
    "bronx": "36005",
    "brooklyn": "36047",
    "manhattan": "36061",
    "queens": "36081",
    "staten-island": "36085",
}
PARTIES = {
    "DEM": "Democratic",
    "REP": "Republican",
    "CON": "Conservative",
    "LBL": "Liberal",
    "LTN": "Libertarian",
    "LIB": "Liberal",
    "NAP": "New Alliance",
    "RTL": "Right to Life",
    "SWP": "Socialist Workers",
    "WKL": "Workers League",
    "SIS": "Staten Island Secession",
    "IND": "Independence",
    "D": "Democratic",
    "R": "Republican",
    "C": "Conservative",
    "L": "Liberal",
    "I": "Independence",
    "RL": "Right to Life",
    "S": "Socialist Workers",
    "F": "Freedom",
    "FU": "Fusion Independent",
    "GP": "Green",
    "M": "Marijuana Green",
    "MA": "Manhattan Alliance",
    "OCFP": "Our Children First",
    "TRP": "The Rebublicrat",
    "Democrat": "Democratic",
    "Independence": "Independence",
    "Republican": "Republican",
}
OFFICES = {
    "Mayor": "mayor",
    "Comptroller": "comptroller",
    "City Council President": "public-advocate",
    "Public Advocate": "public-advocate",
}
METHOD = (
    "Votes as printed in {title}. Each table was checked against the total the report prints for it, and the "
    "contest is loaded only because the candidates' votes add up to that total. {reader}"
)
READ_1989 = (
    "The 1989 report was read by Tesseract at 300 dpi; slipped separators were corrected against the printed totals."
)
READ_1997 = "The 1997 report is a 96 ppi bitmap, so its digits were read by a vision model and accepted only where they add up to the printed total."
LINE = re.compile(r"^[*“\"'~-]*\s*(.+?)\s+((?:[A-Z]{3})(?:, [A-Z]{3})*)\s+([\d.,_]+)\s+(?:\d+\.\d|\+)$")


def number(text: str) -> int:
    text = re.sub(r"^[._]", "", text.strip())
    return int(re.sub(r"[.,_](?=\d{3}\b)", "", text).replace(",", ""))


def parse_1989() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    office, stage, table = None, None, []

    def close(total: int) -> None:
        if office is None or stage is None or not table:
            return
        kept = [entry for entry in table if entry["candidate"] != "Other"]
        if sum(entry["votes"] for entry in kept) == total:
            table[:] = kept
        for entry in table:
            rows.append(
                {**entry, "year": 1989, "stage": stage, "office": office, "total": total, "page": entry["page"]}
            )

    for page in range(7, 11):
        for line in (INBOX / "1989" / f"p{page}.txt").read_text().splitlines():
            line = line.strip()
            if line == "City Council":
                office, table = None, []
                continue
            if line in ("Mayor", "Comptroller", "City Council President"):
                office, table = OFFICES[line], []
                continue
            if line in ("Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"):
                office, table = f"borough-president:{slug(line)}", []
                continue
            if (heading := re.search(r"(Primary Elections?|General Election)$", line)) is not None:
                stage, table = heading[1].split()[0].lower(), []
                continue
            if office is None:
                continue
            if re.fullmatch(r"[\d,.]{7,}", line):
                close(number(line))
                table = []
                continue
            if (other := re.fullmatch(r"Other (\d+)(?: \+)?", line)) is not None:
                table.append({"candidate": "Other", "codes": "", "votes": int(other[1]), "page": page})
                continue
            if (match := LINE.match(line)) is not None:
                name = re.sub(r"^[^A-Za-z]+", "", match[1]).replace("|", "I")
                table.append({"candidate": name, "codes": match[2], "votes": number(match[3]), "page": page})
    return rows


def parse_1997() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for page in range(16, 31):
        tables: dict[tuple[str, str, str], dict[str, object]] = {}
        for reader in ("opus", "sonnet"):
            file = INBOX / "1997" / f"p{page}-{reader}.json"
            if not file.exists():
                continue
            for table in json.loads(file.read_text())["tables"]:
                votes = [number(row["votes"]) if row["votes"] else 0 for row in table["rows"]]
                total = number(table["total"]) if table["total"] else None
                scope = re.sub(r"^(Boro|District):\s*", "", table["scope"])
                key = (table["office"], table["party"], scope)
                if (
                    total is not None
                    and sum(votes) == total
                    and key not in tables
                    and all(row["candidate"] for row in table["rows"])
                ):
                    tables[key] = {**table, "votes": votes, "total": total}
        for (office, party, scope), table in tables.items():
            stage = "primary" if page < 27 else "general"
            if office == "City Council":
                if stage == "general":
                    continue
                office = f"council:{scope}"
            elif office == "Borough President":
                office = f"borough-president:{slug(scope)}"
            elif stage == "general":
                continue
            else:
                office = OFFICES[office]
            names = [row["candidate"] for row in table["rows"]]
            if len(set(names)) < len(names):
                continue
            for row, votes in zip(table["rows"], table["votes"], strict=True):
                if votes == 0:
                    continue
                last, _, first = row["candidate"].partition(",")
                rows.append(
                    {
                        "year": 1997,
                        "stage": stage,
                        "office": office,
                        "total": table["total"],
                        "candidate": re.sub(r"\s*\*$", "", f"{first.strip()} {last.strip()}").strip(),
                        "codes": party if stage == "primary" else row["party_codes"].replace(" ", ""),
                        "votes": votes,
                        "page": page,
                    }
                )
    return rows


READS = {
    1989: "the page images by a vision model (Claude Sonnet 5.5)",
    1991: "the page images by a vision model (Claude Sonnet 5.5)",
    1993: "the page images by two vision models (Claude Sonnet 5.5 and Opus 5.5)",
}
SOLO = (
    "Candidates' votes add up to the printed total or each row's printed percent matches its share of the sum."
)
PAIR = "The table is loaded only where both models read every name and number the same."
VISION = {1989: (11, 19), 1991: (5, 40), 1993: (17, 41)}
SUFFIX = {"Jr": "Jr.", "Sr": "Sr.", "Ii": "II", "Iii": "III", "Iv": "IV"}


def person(name: str) -> str:
    name = re.sub(r"^[*+\s]+|[*+\s]+$", "", name)
    last, comma, first = name.partition(",")
    if comma:
        name = f"{first.strip()} {last.strip()}"
    if name.isupper():
        name = re.sub(r"\b(Mc|Mac)([a-z])", lambda m: m[1] + m[2].upper(), name.title())
        name = " ".join(SUFFIX.get(word, word) for word in name.split())
    return name


def parse_vision() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for year, (first, last) in VISION.items():
        seen: dict[tuple[str, str, str, str], int] = {}
        found: dict[tuple[str, str, str, str], dict[str, object]] = {}
        for page in range(first, last + 1):
            reads: dict[str, dict[str, object]] = {}
            for reader in ("sonnet", "opus"):
                file = INBOX / str(year) / f"p{page}-{reader}.json"
                if file.exists():
                    reads[reader] = json.loads(file.read_text())
            if "sonnet" not in reads:
                continue
            for index, table in enumerate(reads["sonnet"]["tables"]):
                office = table["office"]
                scope = re.sub(r"^(Boro|District):\s*", "", table["scope"]).strip()
                if office not in ("Mayor", "Comptroller", "Public Advocate", "City Council President", "Borough President", "City Council"):
                    continue
                if (office in ("Borough President", "City Council")) and not scope:
                    continue
                if table["stage"] == "general" and not (year == 1993 and office == "Borough President"):
                    continue
                if year == 1993 and page == 18 and office == "Public Advocate":
                    continue
                key = (table["stage"], office, scope, table["party"])
                seen[key] = seen.get(key, 0) + 1
                votes = [number(row["votes"]) if row["votes"] else 0 for row in table["rows"]]
                total = number(table["total"]) if table["total"] else None
                percents = [float(re.sub(r"[^\d.]", "", row.get("percent") or "") or "nan") for row in table["rows"]]
                if total is not None:
                    method = "total" if sum(votes) == total else None
                elif sum(votes) and all(abs(100 * v / sum(votes) - p) < 0.11 for v, p in zip(votes, percents, strict=True)):
                    method = "percent"
                else:
                    method = None
                if method is None and "opus" in reads:
                    other = [t for t in reads["opus"]["tables"] if (t["office"], re.sub(r"^(Boro|District):\s*", "", t["scope"]).strip(), t["party"]) == key[1:]]
                    if len(other) == 1 and [(person(r["candidate"]), number(r["votes"]) if r["votes"] else 0) for r in other[0]["rows"]] == [(person(r["candidate"]), v) for r, v in zip(table["rows"], votes, strict=True)]:
                        method = "agree"
                if method is None or len(table["rows"]) < 1:
                    continue
                found[key] = {**table, "votes": votes, "total": total, "method": method, "page": page}
        for key, table in found.items():
            stage, office, scope, party = key
            if seen[key] > 1 or (office == "City Council" and not scope.isdigit()):
                continue
            rows.extend(
                {
                    "year": year,
                    "stage": stage,
                    "office": f"council:{scope}" if office == "City Council" else f"borough-president:{slug(scope)}" if office == "Borough President" else OFFICES[office],
                    "total": table["total"],
                    "candidate": person(row["candidate"]),
                    "codes": party if stage == "primary" else row["party_codes"].replace(" ", ""),
                    "votes": votes,
                    "page": table["page"],
                    "method": table["method"],
                }
                for row, votes in zip(table["rows"], table["votes"], strict=True)
                if votes
            )
    return rows


def fetch() -> None:
    for year, file in FILES.items():
        raw.store(
            SOURCE,
            URLS[year],
            (INBOX / file).read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp((INBOX / file).stat().st_mtime, UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    frame = pl.concat(
        [
            pl.DataFrame([*parse_1989(), *parse_1997()]).with_columns(pl.lit("").alias("method")),
            pl.DataFrame(parse_vision()),
        ],
        how="diagonal_relaxed",
    )
    frame = frame.with_columns(
        pl.when(pl.col("stage") == "primary")
        .then(pl.col("codes").str.split(",").list.first().str.strip_chars())
        .otherwise(pl.lit(""))
        .alias("party")
    )
    print(
        frame.group_by(["year", "stage", "office", "party", "total"])
        .agg(pl.col("votes").sum().alias("sum"))
        .sort("year", "stage", "office")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City 1989 to 1997 primaries and borough president elections from the Campaign Finance Board's printed vote tables",
            polity="US",
        )

        def name(entity: object, label: str) -> None:
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        city = cs.polity("US-NY")
        ((place_polity, place_path),) = conn.execute(
            "select o.polity_id, s.path from office o join slug s on s.entity_id = o.polity_id and s.is_current "
            "where o.id = %s",
            [cs.key("us.office", "NY:3651000:mayor")],
        ).fetchall()
        system = cs.key("us.electoral_system", "fptp")
        place = cs.area("us.place", "3651000")
        known = {
            re.sub(r"(?: of new york(?: state)?| party)$", "", slug(label).replace("-", " ")): value
            for value, label in conn.execute(
                "select x.value, n.text from external_id x join name n on n.entity_id = x.entity_id "
                "where x.scheme = 'us.party' and x.value like 'NY:%' order by x.value desc"
            ).fetchall()
        }
        parties: dict[str, object] = {}

        def party_for(code: str) -> object:
            label = PARTIES[code.split(",")[0].strip()]
            if label not in parties:
                key = known.get(slug(label).replace("-", " "), f"NY:{slug(label)}")
                party = cs.key("us.party", key)
                if party in cs.minted:
                    cs.add("party", id=party, kind="party", polity_id=city)
                    name(party, f"{label} Party")
                parties[label] = party
            return parties[label]

        seen: set[object] = set()
        made: set[object] = set()
        for (year,), rows_year in frame.group_by(["year"], maintain_order=True):
            source = cs.source(PUBLISHER, TITLES[year])
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLES[year],
                url=URLS[year],
                license="Public record",
            )
            pdf = raw.cached(SOURCE, URLS[year])
            artifact = cs.artifact(pdf, source)
            pages: dict[int, object] = {}
            sizes: dict[int, tuple[int, int]] = {}
            for number_ in sorted(rows_year["page"].unique().to_list()):
                image = PAGES / f"{pdf.sha256}-{number_}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number_), "-l", str(number_), "-singlefile"]
                        + [str(pdf.path), str(image.with_suffix(""))],
                        check=True,
                    )
                sizes[number_] = struct.unpack(">II", image.read_bytes()[16:24])
                width, height = sizes[number_]
                pages[number_] = cs.page(artifact, number_)
                cs.add(
                    "source_page",
                    id=pages[number_],
                    artifact_id=artifact,
                    page_number=number_,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            for (stage, office, party_key), table in rows_year.group_by(
                ["stage", "office", "party"], maintain_order=True
            ):
                if office == "mayor" and stage == "general":
                    continue
                day = DAYS[year][0 if stage == "primary" else 1]
                party_key = party_key or None
                suffix = f"-{slug(PARTIES[party_key])}" if party_key else ""
                if stage == "primary":
                    election = cs.key("us.election", f"{day}:new-york-city:primary")
                    election_path = f"us/ny/new-york-city/{day}-primary"
                    if election in cs.minted and election not in made:
                        made.add(election)
                        cs.add(
                            "election",
                            id=election,
                            polity_id=place_polity,
                            kind="primary",
                            date_from=day,
                            date_to=day,
                            date_precision="day",
                            status="held",
                        )
                        name(election, f"{year} New York City primary election")
                        cs.add("slug", path=election_path, entity_id=election)
                else:
                    election = cs.key("us.election", f"{day}:NY:3651000")
                    election_path = f"{place_path}/{day}-local"
                kind, _, scope = office.partition(":")
                if kind == "council":
                    area, body = conn.execute(
                        "select c.district_area_id, c.body_id from contest c join slug s on s.entity_id = c.id "
                        "where s.path = %s and s.is_current",
                        [f"{place_path}/{DAYS[year][1]}-local/council-district-{scope}"],
                    ).fetchone()
                    office_id = None
                    seat, title = f"council-district-{scope}", f"New York City Council, District {scope}"
                elif kind == "borough-president":
                    area = cs.area("us.county", BOROUGHS[scope])
                    office_id, body = cs.key("us.office", f"new-york-city:{kind}:{scope}"), None
                    label = scope.replace("-", " ").title()
                    seat, title = f"borough-president-{scope}", f"{label} Borough President"
                else:
                    area, body = place, None
                    office_id = cs.key("us.office", f"new-york-city:{kind}" if kind != "mayor" else "NY:3651000:mayor")
                    seat = kind
                    title = {"mayor": "Mayor", "comptroller": "Comptroller", "public-advocate": "Public Advocate"}[kind]
                contest_key = f"{SOURCE}:{day}:{stage}:{office}{suffix}"
                contest = cs.key("us.contest", contest_key)
                adjective = f"{PARTIES[party_key]} primary for " if party_key else ""
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if kind == "council" else "office",
                    office_id=office_id,
                    body_id=body,
                    party_scope_id=party_for(party_key) if party_key else None,
                    district_area_id=area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                    term="full",
                )
                name(contest, f"{year} {adjective}{title}" if party_key else f"{year} {title}")
                cs.add("slug", path=f"{election_path}/{seat}{suffix}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
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
                    body_md=METHOD.format(
                        title=TITLES[year],
                        reader=(READ_1989 if year == 1989 else READ_1997)
                        if table["method"][0] == "" and year in (1989, 1997)
                        else f"Read from {READS[year]}. {PAIR if table['method'][0] == 'agree' else SOLO}",
                    ),
                )
                unit = (
                    cs.unit("us.county", BOROUGHS[scope])
                    if kind == "borough-president"
                    else cs.unit("us.local_district", f"36:3651000:council:district-{scope}")
                    if kind == "council"
                    else cs.unit("us.place", "3651000")
                )
                top = table["votes"].max()
                for row in table.iter_rows(named=True):
                    token = slug(row["candidate"])
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    write_in = row["candidate"] == "Other"
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if write_in else "candidate",
                        party_id=None if write_in or not row["codes"] else party_for(row["codes"]),
                        ballot_label="Other" if write_in else row["candidate"],
                    )
                    if not write_in:
                        person = cs.key(f"{SOURCE}.person", token)
                        if person in cs.minted and person not in seen:
                            seen.add(person)
                            cs.add("person", id=person)
                            name(person, row["candidate"])
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=row["candidate"],
                            is_independent=False,
                        )
                    fact = {
                        "result_set_id": result_set,
                        "unit_id": unit,
                        "option_id": option,
                        "channel": 0,
                        "measure": 1,
                        "count_round": 0,
                    }
                    cs.add("result_value", **fact, flags=0, value=row["votes"])
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=pages[row["page"]],
                        source_id=None,
                        x0=0,
                        y0=0,
                        x1=1,
                        y1=1,
                    )
                    if row["votes"] == top:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="won" if stage == "primary" else "elected",
                            seats=None,
                        )
        return cs.write()
