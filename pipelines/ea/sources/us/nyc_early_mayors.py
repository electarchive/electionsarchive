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
YEARS = sorted([*range(1897, 1986, 4), *SPECIALS])
TITLE = "New York City mayoral election articles, results boxes"
NOTE = (
    "Citywide votes as tabulated in the results boxes of the English Wikipedia's articles on each New York City mayoral "
    "election, a stopgap until the Board of Elections' printed canvass is read."
)
METHOD = (
    "Citywide votes from the results box of the English Wikipedia's article on the {year} New York City mayoral "
    "election. A candidate on several ballot lines carries the total of the lines; the lines themselves are not "
    "shown. The article's own total row is ignored."
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


def page(year: int) -> str:
    return API + urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content|ids",
            "rvslots": "main",
            "titles": f"{year} New York City {'special ' if year in SPECIALS else ''}mayoral election",
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


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year in YEARS:
        wikitext = json.loads(raw.cached(SOURCE, page(year)).path.read_text())["query"]["pages"][0]["revisions"][0][
            "slots"
        ]["main"]["content"]
        if year == 1925:
            rows.extend(
                {"year": year, "candidate": name, "line": line, "votes": votes, "won": name == "Jimmy Walker"}
                for name, line, votes in TABLE_1925
            )
            continue
        general = next(
            box
            for box in re.finditer(
                r"\{\{Election box begin.*?\{\{Election box end\}\}", wikitext, re.DOTALL | re.IGNORECASE
            )
            if re.search(r"title\s*=[^\n]*mayoral election", box.group(0), re.IGNORECASE)
        ).group(0)
        for kind, body in re.findall(
            r"\{\{Election box (winning candidate|candidate)[^|{]*?\s*\n?\s*\|(.*?)\}\}\s*(?=\n|$)",
            general,
            re.DOTALL | re.IGNORECASE,
        ):
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
                    "line": re.sub(r"\s+", " ", text(fields["party"])),
                    "votes": int(votes) if votes else None,
                    "won": kind.lower() == "winning candidate",
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City mayoral elections 1897–1985, citywide, from the English Wikipedia's election articles",
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
        for (year,), rows in frame.group_by(["year"], maintain_order=True):
            first = date(year, 11, 1)
            day = SPECIALS.get(year, first + timedelta(days=(0 - first.weekday()) % 7 + 1))
            kind = "general" if year % 2 == 0 else "local"
            parent_key = f"{day}:NY"
            parent = cs.key("us.election", parent_key)
            if parent in cs.minted:
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
            election = cs.key("us.election", f"{day}:NY:3651000")
            if election in cs.minted:
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
            contest_key = f"nyc_early_mayors:{day}:NY:3651000:mayor"
            contest = cs.key("us.contest", contest_key)
            path = f"{polity_path}/{day}-{kind}/mayor"
            if path in taken and contest in cs.minted:
                raise ValueError(f"{path} is already taken")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=owner,
                party_scope_id=None,
                district_area_id=cs.area("us.place", "3651000"),
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
                term="remainder" if year in SPECIALS else "full",
            )
            name(contest, f"{year} New York City {'special ' if year in SPECIALS else ''}mayoral election")
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
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD.format(year=year))
            unit = cs.unit("us.place", "3651000")
            candidates: list[tuple[str, str, int, bool]] = []
            for (candidate,), lines in rows.group_by(["candidate"], maintain_order=True):
                counted = lines.filter(pl.col("line") != "Total")
                total = lines.filter(pl.col("line") == "Total")["votes"]
                votes = int(total[0] if len(total) else counted["votes"].sum())
                if votes:
                    candidates.append((candidate, counted["line"][0], votes, bool(lines["won"].any())))
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
        return cs.write()
