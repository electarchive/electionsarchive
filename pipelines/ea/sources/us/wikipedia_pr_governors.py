import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.wikipedia_guam_governors import count, fields_of, plain, templates_of
from ea.store import DATA, connect

SOURCE = "us.wikipedia_pr_governors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "Puerto Rican general election articles, governor results"
NOTE = (
    "Island-wide votes for governor as tabulated in the English Wikipedia's article on each Puerto Rican general "
    "election, which cites Nohlen's Elections in the Americas and the State Elections Commission."
)
ELECTIONS = {
    1976: date(1976, 11, 2),
    1980: date(1980, 11, 4),
    1984: date(1984, 11, 6),
    1988: date(1988, 11, 8),
    1992: date(1992, 11, 3),
    1996: date(1996, 11, 5),
    2000: date(2000, 11, 7),
    2004: date(2004, 11, 2),
    2008: date(2008, 11, 4),
    2012: date(2012, 11, 6),
    2016: date(2016, 11, 8),
    2024: date(2024, 11, 5),
}
PARTIES = {
    "Puerto Rican Renewal Party": "puerto-rico-renewal",
    "Working People's Party": "party-of-the-working-people",
    "Movimiento Unión Soberanista": "sovereignty-union-movement",
    "Proyecto Dignidad": "dignity-project",
    "Movimiento Victoria Ciudadana": "citizens-victory-movement",
}


def titles() -> list[str]:
    return [f"{year} Puerto Rican general election" for year in ELECTIONS]


def page() -> str:
    return API + urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content|ids",
            "rvslots": "main",
            "titles": "|".join(titles()),
            "format": "json",
            "formatversion": 2,
        }
    )


def fetch() -> None:
    raw.fetch(SOURCE, page(), suffix=".json")


def parse() -> None:
    texts = {
        found["title"]: re.sub(
            r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->",
            "",
            found["revisions"][0]["slots"]["main"]["content"],
            flags=re.DOTALL,
        )
        for found in json.loads(raw.cached(SOURCE, page()).path.read_text())["query"]["pages"]
    }
    rows: list[dict[str, object]] = []
    for year, day in ELECTIONS.items():
        text = texts[f"{year} Puerto Rican general election"]
        start = text.index("{{Election results", text.index("Governor===", text.index("==Results==")))
        _, fields = fields_of(templates_of(text[start:])[0])
        for index in range(1, 12):
            if f"cand{index}" in fields:
                rows.append(
                    {
                        "year": year,
                        "day": day,
                        "position": index,
                        "candidate": re.sub(r" \([^)]*\)$", "", plain(fields[f"cand{index}"]))
                        .replace("Iturregu", "Iturregui")
                        .replace("Berríos Martínez", "Berríos"),
                        "party": re.sub(r" \(Puerto Rico\)$", "", plain(fields.get(f"party{index}", ""))),
                        "votes": count(fields[f"votes{index}"]),
                    }
                )
            elif f"row{index}" in fields:
                rows.append(
                    {
                        "year": year,
                        "day": day,
                        "position": index,
                        "candidate": "Write-ins" if "rite" in fields[f"row{index}"] else "Others",
                        "party": "",
                        "votes": count(fields[f"votes{index}"]),
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico governor general elections 1976–2016 and 2024 from the English Wikipedia's election articles",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/2004_Puerto_Rican_general_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, page()), source)
        plurality = cs.key("us.electoral_system", "fptp")
        unit, area = conn.execute(
            """select u.id, u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id where p.iso_code = 'US-PR' and u.scheme_code = 'us.state'"""
        ).fetchone()
        polity = cs.polity("US-PR")
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        held = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value like '%%:PR:governor'"
            ).fetchall()
        }
        office = cs.key("us.office", "governor:PR")
        if office in cs.minted:
            raise ValueError("the Puerto Rico governor office must exist")
        persons: dict[str, UUID] = {}
        for (year, day), rows in frame.group_by(["year", "day"], maintain_order=True):
            contest_key = f"{day}:PR:governor"
            if contest_key in held:
                continue
            election_key = f"{day}:PR"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind="general",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{year} Puerto Rico general election")
                cs.add("slug", path=f"us/pr/{day}-general", entity_id=election)
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
                electoral_system_id=plurality,
                default_result_kind="counted",
            )
            name(contest, f"{year} Puerto Rico governor election")
            cs.add("slug", path=f"us/pr/{day}-general/governor", entity_id=contest)
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
                body_md=f"Island-wide votes from the English Wikipedia's article on the {year} Puerto Rican general election, citing Nohlen's Elections in the Americas and the State Elections Commission.",
            )
            ranked: list[tuple[int, UUID]] = []
            for candidate, party, votes in rows.sort("position").select("candidate", "party", "votes").iter_rows():
                if candidate in ("Write-ins", "Others"):
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if candidate == "Write-ins" else "others",
                        ballot_label=candidate,
                    )
                else:
                    party_id = None
                    if party and party != "Independent":
                        party_key = f"PR:{PARTIES.get(party, slug(party))}"
                        party_id = cs.key("us.party", party_key)
                        if party_key not in parties_known:
                            parties_known.add(party_key)
                            cs.add("party", id=party_id, kind="party", polity_id=polity)
                            name(party_id, party)
                    token = slug(candidate)
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=candidate,
                    )
                    if token not in persons:
                        persons[token] = cs.key(f"{SOURCE}.person", f"PR:{token}")
                        if persons[token] in cs.minted:
                            cs.add("person", id=persons[token])
                            name(persons[token], candidate)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                        person_id=persons[token],
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=party_id is None,
                    )
                    ranked.append((votes, option))
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
            ranked.sort(key=lambda pair: -pair[0])
            cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="elected", seats=None)
        return cs.write()
