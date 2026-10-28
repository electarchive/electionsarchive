import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_early_mayors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
SPECIALS = {1932: date(1932, 11, 8), 1950: date(1950, 11, 7)}
PRIMARIES = {
    1925: date(1925, 9, 15),
    1929: date(1929, 9, 17),
    1933: date(1933, 9, 19),
    1937: date(1937, 9, 16),
    1941: date(1941, 9, 16),
    1961: date(1961, 9, 7),
    1965: date(1965, 9, 14),
    1977: date(1977, 9, 8),
    1985: date(1985, 9, 10),
}
YEARS = sorted([*range(1897, 1986, 4), *SPECIALS])
CITYWIDE = {
    "comptroller": ("Comptroller", "new-york-city:comptroller", (1993, 1997)),
    "public-advocate": ("Public Advocate", "new-york-city:public-advocate", (1993, 1997)),
}
TITLE = "New York City mayoral election articles, results boxes"
NOTE = (
    "Citywide votes as tabulated in the results boxes of the English Wikipedia's articles on each New York City mayoral "
    "election, a stopgap until the Board of Elections' printed canvass is read."
)
METHOD = (
    "Citywide votes from the results box of the English Wikipedia's article on the {year} New York City {office}. "
    " A candidate on several ballot lines carries the total of the lines; the lines themselves are not "
    "shown. The article's own total row is ignored."
)
BOROUGH_METHOD = (
    "Votes by borough from the borough table in the English Wikipedia's article on the {year} New York City mayoral "
    "election, for the candidates the table lists whose borough figures add up to their citywide total. A stopgap "
    "until the Board of Elections' printed canvass is read."
)
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Socialist Party of America": "socialist-party-of-america",
    "Socialist Labor Party of America": "socialist-labor-party-of-america",
    "Prohibition Party": "prohibition-party",
    "Libertarian Party": "libertarian-party",
    "Communist Party USA": "NY:communist",
}
TABLE_1925 = [
    ("Jimmy Walker", "Democratic Party", 748687),
    ("Frank D. Waterman", "Republican Party", 346564),
    ("Norman Thomas", "Socialist Party of America", 39574),
    ("Joseph Brandon", "Socialist Labor Party of America", 1643),
    ("Warren Fisher", "Progressive", 1498),
]
NAMES = {
    "Fiorello LaGuardia": "Fiorello La Guardia",
    "John Francis Hylan": "John F. Hylan",
    "John P. Mitchel": "John Purroy Mitchel",
    "Eric Haas": "Eric Hass",
    "Vito Battista": "Vito P. Battista",
    "Robert F. Wagner Jr.": "Robert F. Wagner Jr.",
}


def page(year: int, office: str = "mayor") -> str:
    return API + urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content|ids",
            "rvslots": "main",
            "titles": f"{year} New York City {CITYWIDE[office][0] + ' election' if office in CITYWIDE else ('special ' if year in SPECIALS else '') + 'mayoral election'}",
            "redirects": 1,
            "format": "json",
            "formatversion": 2,
        }
    )


def text(value: str) -> str:
    value = re.sub(r"<ref[^>]*?/>|<ref.*?</ref>|\{\{efn.*?\}\}", "", value, flags=re.DOTALL)
    value = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", value)
    return re.sub(r"\{\{[^}]*\}\}", "", re.sub(r"'{2,}", "", value)).strip()


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, page(year), suffix=".json")
    for office, (_, _, years) in CITYWIDE.items():
        for year in years:
            raw.fetch(SOURCE, page(year, office), suffix=".json")


def parse() -> None:
    rows: list[dict[str, object]] = []
    boroughs: list[dict[str, object]] = []
    for year in YEARS:
        wikitext = json.loads(raw.cached(SOURCE, page(year)).path.read_text())["query"]["pages"][0]["revisions"][0][
            "slots"
        ]["main"]["content"]
        for table in re.finditer(r"\{\|.*?\n\|\}", wikitext, re.DOTALL):
            cells = table.group(0)
            lines = re.split(r"\n\|-[^\n]*", cells)
            if (
                "Manhattan" not in cells
                or "Bronx and Manhattan" in cells
                or re.search("primary", lines[1], re.IGNORECASE)
            ):
                continue
            for line in lines[2:]:
                fields = []
                for entry in line.strip().split("\n"):
                    if entry.startswith(("|", "!")):
                        fields.extend(re.split(r"\|\|", entry[1:]))
                fields = [
                    re.sub(r"</?u>", "", text(re.sub(r"^\s*(?:style|bgcolor|rowspan|colspan)[^|]*\|(?!\|)", "", field)))
                    for field in fields
                ]
                counts = [
                    int(field.replace(",", "")) for field in fields if re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d+", field)
                ]
                if len(counts) >= 6 and sum(counts[-6:-1]) == counts[-1] and fields[0]:
                    boroughs.append({"year": year, "candidate": NAMES.get(fields[0], fields[0]), "counts": counts[-6:]})
        if year == 1925:
            rows.extend(
                {"year": year, "candidate": name, "line": line, "votes": votes, "won": name == "Jimmy Walker"}
                for name, line, votes in TABLE_1925
            )
        boxes = [
            (box.group(0), "general", "")
            for box in re.finditer(
                r"\{\{Election box begin.*?\{\{Election box end\}\}", wikitext, re.DOTALL | re.IGNORECASE
            )
            if year != 1925 and re.search(r"title\s*=[^\n]*mayoral election", box.group(0), re.IGNORECASE)
        ][:1]
        if year in PRIMARIES:
            boxes += [
                (box.group(0), "primary", heading[1])
                for box in re.finditer(
                    r"\{\{Election box begin.*?\{\{Election box end\}\}", wikitext, re.DOTALL | re.IGNORECASE
                )
                if (
                    heading := re.search(
                        r"title\s*=\s*\d{4} (?:New York City )?(Democratic|Republican) (?:mayoral )?primary(?! runoff)",
                        box.group(0),
                    )
                )
            ]
        for stage, party, kind, body in [
            (stage, party, kind, body)
            for box, stage, party in boxes
            for kind, body in re.findall(
                r"\{\{Election box (winning candidate|candidate)[^|{]*?\s*\n?\s*\|(.*?)\}\}\s*(?=\n|$)",
                box,
                re.DOTALL | re.IGNORECASE,
            )
        ]:
            fields = {
                key.strip(): value.strip()
                for part in re.split(r"\n?\s*\|\s*(?=\w+\s*=)", "|" + body)
                if "=" in part
                for key, value in [part.split("=", 1)]
            }
            votes = re.sub(r"\D", "", text(fields.get("votes", "")))
            name = re.sub(r"\s*\([^)]*\)", "", text(fields["candidate"])).strip()
            rows.append(
                {
                    "year": year,
                    "candidate": NAMES.get(name, name),
                    "line": f"{party} Party" if party else re.sub(r"\s+", " ", text(fields["party"])),
                    "votes": int(votes) if votes else None,
                    "won": kind.lower() == "winning candidate",
                    "stage": stage,
                    "party": party,
                }
            )
    for office, (label, _, years) in CITYWIDE.items():
        for year in years:
            wikitext = json.loads(raw.cached(SOURCE, page(year, office)).path.read_text())["query"]["pages"][0][
                "revisions"
            ][0]["slots"]["main"]["content"]
            box = next(
                (
                    box.group(0)
                    for box in re.finditer(
                        r"\{\{Election box begin.*?\{\{Election box end\}\}", wikitext, re.DOTALL | re.IGNORECASE
                    )
                    if re.search(rf"title\s*=\s*{year} New York City {label} election", box.group(0))
                ),
                None,
            )
            if box is not None:
                for kind, body in re.findall(
                    r"\{\{Election box (winning candidate|candidate)[^|{]*?\s*\n?\s*\|(.*?)\}\}\s*(?=\n|$)",
                    box,
                    re.DOTALL | re.IGNORECASE,
                ):
                    fields = {
                        key.strip(): value.strip()
                        for part in re.split(r"\n?\s*\|\s*(?=\w+\s*=)", "|" + body)
                        if "=" in part
                        for key, value in [part.split("=", 1)]
                    }
                    votes = re.sub(r"\D", "", text(fields.get("votes", "")))
                    rows.append(
                        {
                            "year": year,
                            "office": office,
                            "candidate": re.sub(r"\s*\([^)]*\)", "", text(fields["candidate"])).strip(),
                            "line": re.sub(r"\s+", " ", text(fields["party"])),
                            "votes": int(votes) if votes else None,
                            "won": kind.lower() == "winning candidate",
                        }
                    )
                continue
            general = re.search(
                r"==\s*General election\s*==.*?(\{\|.*?\n\|\}|\Z)|(\{\|[^\n]*\n(?:(?!\|\}).)*general election.*?\n\|\})",
                wikitext,
                re.DOTALL | re.IGNORECASE,
            )
            general = (general[1] or general[2]) if general else ""
            for line in re.split(r"\n\|-[^\n]*", general)[2:]:
                fields = [
                    text(re.sub(r"^\s*(?:style|bgcolor|rowspan|colspan|align)[^|]*\|(?!\|)", "", field))
                    for entry in line.strip().split("\n")
                    if entry.startswith("|")
                    for field in re.split(r"\|\|", entry[1:])
                ]
                if len(fields) >= 3 and fields[0] != "Total" and re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d+", fields[2]):
                    rows.append(
                        {
                            "year": year,
                            "office": office,
                            "candidate": re.sub(r"\s*\([^)]*\)", "", fields[0]).strip(),
                            "line": fields[1],
                            "votes": int(fields[2].replace(",", "")),
                            "won": False,
                        }
                    )
    for row in rows:
        row.setdefault("office", "mayor")
        row.setdefault("stage", "general")
        row.setdefault("party", "")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "lines.parquet")
    pl.DataFrame(boroughs).write_parquet(PARSED / "boroughs.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    boroughs = pl.read_parquet(PARSED / "boroughs.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City mayoral elections 1897–1985, and comptroller and public advocate 1993 and 1997, citywide, from the English Wikipedia's election articles",
            polity="US",
        )

        def name(entity: object, label: str) -> None:
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/1925_New_York_City_mayoral_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, page(year)), source)
        owner = cs.key("us.office", "NY:3651000:mayor")
        ((polity, polity_path),) = conn.execute(
            "select o.polity_id, s.path from office o join slug s on s.entity_id = o.polity_id and s.is_current "
            "where o.id = %s",
            [owner],
        ).fetchall()
        system = cs.key("us.electoral_system", "fptp")
        known_parties = {
            re.sub(r"(?: of new york(?: state)?| party)$", "", slug(label).replace("-", " ")): value
            for value, label in conn.execute(
                "select x.value, n.text from external_id x join name n on n.entity_id = x.entity_id "
                "where x.scheme = 'us.party' and x.value like 'NY:%' order by x.value desc"
            ).fetchall()
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/ny/%'").fetchall()}
        parties: dict[str, object | None] = {}
        seen: set[object] = set()
        offices = {
            "mayor": ("mayoral election", owner),
            **{office: (f"{label} election", cs.key("us.office", key)) for office, (label, key, _) in CITYWIDE.items()},
        }
        for (year, office_slug, stage, scope), rows in frame.group_by(
            ["year", "office", "stage", "party"], maintain_order=True
        ):
            office_label, office_id = offices[office_slug]
            first = date(year, 11, 1)
            primary = stage == "primary"
            day = (
                PRIMARIES[year]
                if primary
                else SPECIALS.get(year, first + timedelta(days=(0 - first.weekday()) % 7 + 1))
            )
            kind = "primary" if primary else "general" if year % 2 == 0 else "local"
            if primary:
                election = cs.key("us.election", f"{day}:new-york-city:primary")
                if election in cs.minted and election not in seen:
                    seen.add(election)
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        kind="primary",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    name(election, f"{year} New York City primary election")
                    cs.add("slug", path=f"{polity_path}/{day}-primary", entity_id=election)
            elif (parent := cs.key("us.election", f"{day}:NY")) in cs.minted:
                cs.add(
                    "election",
                    id=parent,
                    polity_id=cs.polity("US-NY"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{year} New York {kind} elections")
                cs.add("slug", path=f"us/ny/{day}-{kind}", entity_id=parent)
            if not primary:
                election = cs.key("us.election", f"{day}:NY:3651000")
            if not primary and election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{year} New York City {kind} election")
                cs.add("slug", path=f"{polity_path}/{day}-{kind}", entity_id=election)
            suffix = f"-{scope.lower()}" if primary else ""
            contest_key = f"nyc_early_mayors:{day}:{'primary:' if primary else ''}NY:3651000:{office_slug}{suffix}"
            contest = cs.key("us.contest", contest_key)
            path = f"{polity_path}/{day}-{kind}/{office_slug}{suffix}"
            if path in taken and contest in cs.minted:
                raise ValueError(f"{path} is already taken")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                party_scope_id=cs.key("us.party", PARTIES[scope]) if primary else None,
                district_area_id=cs.area("us.place", "3651000"),
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
                term="remainder" if year in SPECIALS else "full",
            )
            name(
                contest,
                f"{year} {scope + ' primary for ' if primary else ''}New York City {'special ' if year in SPECIALS else ''}{office_label}",
            )
            cs.add("slug", path=path, entity_id=contest)
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
                "note", entity_id=result_set, kind="methodology", body_md=METHOD.format(year=year, office=office_label)
            )
            unit = cs.unit("us.place", "3651000")
            candidates: list[tuple[str, str, int, bool]] = []
            for (candidate,), lines in rows.group_by(["candidate"], maintain_order=True):
                counted = lines.filter(pl.col("line") != "Total")
                total = lines.filter(pl.col("line") == "Total")["votes"]
                votes = int(total[0] if len(total) else counted["votes"].sum())
                if votes:
                    candidates.append((candidate, counted["line"][0], votes, bool(lines["won"].any())))
            if primary:
                everyone = sum(votes for _, _, votes, _ in candidates)
                candidates = [(*candidate[:3], candidate[2] * 2 > everyone) for candidate in candidates]
            elif not any(won for *_, won in candidates):
                top = max(candidates, key=lambda candidate: candidate[2])
                candidates = [(*candidate[:3], candidate == top) for candidate in candidates]
            for candidate, line, votes, won in candidates:
                label = re.sub(r"\s*\((?:US|United States)\)\s*$", "", line)
                label = re.sub(r"\s+", " ", label).strip()
                key = (
                    next(
                        (value for prefix, value in PARTIES.items() if label.startswith(prefix)),
                        known_parties.get(
                            re.sub(r"(?: of new york(?: state)?| party)$", "", slug(label).replace("-", " "))
                        ),
                    )
                    or f"NY:{slug(re.sub(r' Party$', '', label))}"
                )
                party = parties.get(key)
                if party is None:
                    party = parties[key] = cs.key("us.party", key)
                    if party in cs.minted:
                        cs.add("party", id=party, kind="party", polity_id=cs.polity("US-NY"))
                        name(party, label)
                token = slug(candidate)
                person = cs.key(f"{SOURCE}.person", token)
                if person in cs.minted and person not in seen:
                    seen.add(person)
                    cs.add("person", id=person)
                    name(person, candidate)
                option = cs.key("us.option", f"{contest_key}:{token}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
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
                    flags=0,
                    value=votes,
                )
                if won:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
            matched = {
                candidate: row["counts"]
                for candidate, _, votes, _ in candidates
                for row in boroughs.filter(pl.col("year") == year).iter_rows(named=True)
                if row["counts"][-1] == votes
                and re.sub(r"[^a-z]", "", candidate.split()[-1].lower().replace("jr.", ""))
                in re.sub(r"[^a-z]", "", row["candidate"].lower())
            }
            if matched:
                by_borough = cs.key(f"{SOURCE}.result_set", f"{contest_key}:boroughs")
                cs.add(
                    "result_set",
                    id=by_borough,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=False,
                    completeness=round(
                        sum(counts[-1] for counts in matched.values()) / sum(v for _, _, v, _ in candidates), 4
                    ),
                )
                cs.add("note", entity_id=by_borough, kind="methodology", body_md=BOROUGH_METHOD.format(year=year))
                for candidate, counts in matched.items():
                    for fips, count in zip(("36061", "36005", "36047", "36081", "36085"), counts[:5], strict=True):
                        cs.add(
                            "result_value",
                            result_set_id=by_borough,
                            unit_id=cs.unit("us.county", fips),
                            option_id=cs.key("us.option", f"{contest_key}:{slug(candidate)}"),
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=count,
                        )
        return cs.write()
