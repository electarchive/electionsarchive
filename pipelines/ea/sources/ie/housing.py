"""European Parliament elections in Ireland, 2004 to 2019, from the Department of Housing's results volumes.

Each election date is one package, `ie.housing/ep-<year>`, built on `ie.housing/geo` (the constituencies and their
reporting units) and `ie.housing/registry` (the source, the parties, and the electoral system). A constituency is one
contest with one result set. The volumes print each candidate's running total after every count, and the
non-transferable papers, which the package stores under the count that closes them.
"""

import re
from datetime import date
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ie import ep_text

SOURCE = "ie.housing"
PORTAL = "https://opendata.housing.gov.ie"
IRELAND = str(uuid5(package.NAMESPACE, "polity\x1fIE"))
SCHEME = "ie.ep_constituency"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED, QUOTA = 1, 2, 3, 4, 5, 21, 26
DERIVED = 1
VOLUMES = {
    2004: (
        date(2004, 6, 11),
        (
            f"{PORTAL}/en_GB/dataset/f07aa87c-5c2d-40fa-8d49-a42e94fda7b1/resource/0a53bf7b-5b5c-48e6-8932-c29245bbc371"
            "/download/2004-european-parliament-election-results.pdf"
        ),
        "2004-european-parliament-election-results",
    ),
    2009: (
        date(2009, 6, 5),
        (
            f"{PORTAL}/en_GB/dataset/3d0d64c7-bf99-4386-8d18-bd34e41ad455/resource/e94b736e-6861-4283-9bbb-73bc2b5dfe8f"
            "/download/european-parliament-election-results-2009.pdf"
        ),
        "2009-european-parliament-election-results",
    ),
    2014: (
        date(2014, 5, 23),
        (
            f"{PORTAL}/dataset/7c9deef1-3ba6-40e2-a205-42eb7c28c166/resource/03f86916-57ab-4962-b2e2-b9dfc196a643"
            "/download/european-parliament-election-results-2014.pdf"
        ),
        "2014-european-parliament-election-results",
    ),
    2019: (
        date(2019, 5, 24),
        (
            f"{PORTAL}/dataset/22099b9d-8fb6-4b82-9a57-f09cc8c60f1b/resource/3f497854-9794-40ae-b2ce-07373e207145"
            "/download/european-parliament-election-results-2019.pdf"
        ),
        "2019-european-parliament-election-results",
    ),
}
# The constituencies in force for each election: the Constituency Commission's four of 2003, then the three of 2013.
FOUR = ("dublin-2004", "east-2004", "north-west-2004", "south-2004")
THREE = ("dublin-2014", "midlands-north-west-2014", "south-2014")
AREAS = {
    "DUBLIN": {2004: "dublin-2004", 2009: "dublin-2004", 2014: "dublin-2014", 2019: "dublin-2014"},
    "EAST": {2004: "east-2004", 2009: "east-2004"},
    "NORTH-WEST": {2004: "north-west-2004", 2009: "north-west-2004"},
    "SOUTH": {2004: "south-2004", 2009: "south-2004", 2014: "south-2014", 2019: "south-2014"},
    "MIDLANDS - NORTH - WEST": {2014: "midlands-north-west-2014", 2019: "midlands-north-west-2014"},
}
TITLES = {
    "dublin-2004": "Dublin",
    "east-2004": "East",
    "north-west-2004": "North-West",
    "south-2004": "South",
    "dublin-2014": "Dublin",
    "midlands-north-west-2014": "Midlands–North-West",
    "south-2014": "South",
}
PARTIES = {
    "FF": ("Fianna Fáil", "F.F."),
    "FG": ("Fine Gael", "F.G."),
    "LAB": ("Labour Party", "Lab."),
    "SF": ("Sinn Féin", "S.F."),
    "GP": ("Green Party", "G.P."),
    "SP": ("Socialist Party", "S.P."),
    "SD": ("Social Democrats", "S.D."),
    "WP": ("Workers' Party", "W.P."),
    "I4C": ("Independents 4 Change", "I.4.C."),
    "LIB": ("Libertas", "Lib."),
    "DDI": ("Direct Democracy Ireland", "D.D.I."),
    "CSP": ("Christian Solidarity Party", "C.S.P."),
    "PBPA": ("People Before Profit Alliance", "P.B.P.A."),
    "SPBP": ("Solidarity–People Before Profit", "S.P.B.P."),
    "RI": ("Renua Ireland", "R.I."),
    "FN": ("Fís Nua", "F.N."),
    "CD": ("Catholic Democrats", "C.D."),
    "II": ("Identity Ireland", "I.I."),
}
SYSTEM = {"formula": "stv", "quota": "droop", "surplus": "last_parcel"}
METHOD = (
    "Ireland elects its members of the European Parliament by single transferable vote in multi-member "
    "constituencies. The quota is the Droop quota: valid votes divided by one more than the number of seats, plus "
    "one, with any fraction ignored. A candidate who reaches the quota is elected, and their surplus passes to the "
    "next preference. A later surplus passes on only the last parcel received. When no surplus is left, the lowest "
    "candidate is eliminated and their papers pass on. Papers with no further usable preference are non-transferable. "
    "The last seats can be filled without the quota once the continuing candidates equal the seats left."
)
VOLUME_NOTE = (
    "Each candidate's votes are the running total after each count, as the volume prints them: count 1 is the first "
    "preferences, and a candidate eliminated at an earlier count shows nothing, stored as zero. The non-transferable "
    "papers are the running total of papers that could no longer be transferred. The ballots cast are the valid poll "
    "plus the invalid ballot papers. The volume prints the electorate, the invalid ballot papers, the valid poll, "
    "the seats, and the quota for each constituency."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source() -> str:
    return key("source", "dhlgh-opendata")


def area(code: str) -> str:
    return key("area", SCHEME, code)


def unit(code: str) -> str:
    return key("unit", SCHEME, code)


def party(label: str) -> str | None:
    code = re.sub(r"[^A-Z0-9]", "", label.upper())
    return None if code in ("", "NONP") else key("party", code)


def fetch() -> None:
    for _, url, _ in VOLUMES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/ep-{year}" for year in VOLUMES)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in VOLUMES:
        ep(year).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The European Parliament constituencies of Ireland for the 2004, 2009, 2014, and 2019 elections, and the "
        "reporting unit of each",
    )
    p.external |= {IRELAND, SCHEME}
    for code, title in TITLES.items():
        id = area(code)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=code)
        p.name(id, title, "en", "official")
        p.add(
            "reporting_unit",
            id=unit(code),
            polity_id=IRELAND,
            scheme_code=SCHEME,
            code=code,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
        era = "2004 and 2009" if code.endswith("2004") else "2014 and 2019"
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="context",
            body_md=(
                f"The {title} constituency as it stood for the {era} European Parliament elections. The code carries "
                "the year of the Act that drew the set, so it never meets the constituencies of another set."
            ),
            lang="en",
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Department of Housing's open data portal as a source, the parties that stood in the 2004 to 2019 "
        "European Parliament elections in Ireland, and their electoral system",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(IRELAND)
    id = source()
    p.add(
        "source",
        id=id,
        kind="official_body",
        publisher="Department of Housing, Local Government and Heritage",
        title="European Parliament election results, Ireland (open data portal)",
        url=f"{PORTAL}/dataset?q=european+parliament+election+results",
        license="Creative Commons Attribution Share-Alike 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="Department of Housing, Local Government and Heritage",
    )
    for _, url, _ in VOLUMES.values():
        p.artifact(raw.cached(SOURCE, url), id)
    system = key("electoral_system", "european-parliament")
    p.add("electoral_system", id=system, polity_id=IRELAND, rules=SYSTEM, rules_version=1)
    p.name(system, "European Parliament elections in Ireland", "en", "official")
    p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=METHOD, lang="en")
    for code, (title, short) in PARTIES.items():
        party_id = key("party", code)
        p.add("party", id=party_id, kind="party", polity_id=IRELAND)
        p.name(party_id, title, "en", "official")
        p.name(party_id, short, "en", "abbreviation", False)
    return p


def display(label: str) -> str:
    """A candidate as people write the name: the printed `SURNAME, First` turned around, in ordinary case."""

    def word(text: str) -> str:
        if text.isupper() and len(text) > 1:
            text = text.title()
            return re.sub(r"^(Mc|Mac(?=[a-z]{3}))([a-z])", lambda hit: hit.group(1) + hit.group(2).upper(), text)
        hit = re.fullmatch(r"(Mc)([A-Z’'\-]+)", text)
        return hit.group(1) + hit.group(2).capitalize() if hit else text

    if "," not in label:
        return " ".join(word(w) for w in label.split())
    surname, first = (part.strip() for part in label.split(",", 1))
    return " ".join(word(w) for w in [*first.split(), *surname.split()])


def national(pages: list[str], year: int) -> dict[str, int] | None:
    """The Republic of Ireland column of a volume's results summary, where the volume prints one."""
    text = "\n".join(pages)
    found: dict[str, int] = {}
    if year == 2014:
        for field, label in (
            ("electorate", "Electorate"),
            ("ballots", "Total Poll"),
            ("invalid", "Invalid Ballot Papers"),
            ("valid", "Valid Poll"),
            ("seats", "Seats"),
        ):
            hit = re.search(rf"^{label}\s+(?:[\d,]+\s+){{3}}([\d,]+)\s*$", text, re.MULTILINE)
            if hit is None:
                return None
            found[field] = int(hit.group(1).replace(",", ""))
        return found
    if year == 2019:
        tail = re.search(
            r"South\s+Republic of Ireland\s*\n\s*([\d,]+)\s+([\d,]+)\s*\n\s*([\d,]+)\s+([\d,]+)\s*\n\s*([\d.]+)%\s+([\d.]+)%"
            r"\s*\n\s*([\d,]+)\s+([\d,]+)\s*\n\s*([\d,]+)\s+([\d,]+)\s*\n\s*(\d+)\s+(\d+)",
            re.sub(r"[ \t]+", " ", text).replace("\n ", "\n"),
        )
        if tail is None:
            return None
        values = [int(tail.group(i).replace(",", "")) for i in range(1, 13) if i not in (5, 6)]
        found = {
            "electorate": values[1],
            "ballots": values[3],
            "invalid": values[5],
            "valid": values[7],
            "seats": values[9],
        }
        return found
    return None


def ep(year: int) -> Package:
    day, url, _ = VOLUMES[year]
    artifact = raw.cached(SOURCE, url)
    pages = ep_text.layout(artifact.path)
    tables = ep_text.tables(pages)
    p = Package(
        f"{SOURCE}/ep-{year}",
        SOURCE,
        f"The {year} European Parliament election in Ireland, count by count in each constituency",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(IRELAND)
    p.uses(artifact)
    election = key("election", day)
    p.add("election", id=election, polity_id=IRELAND, kind="general", date_from=day, date_to=day, status="held")
    p.name(election, f"{year} European Parliament election in Ireland", "en", "official")
    path = f"ie/{day}-european-parliament"
    p.add("slug", path=path, entity_id=election, is_current=True)
    system = key("electoral_system", "european-parliament")
    sums = {"electorate": 0, "invalid": 0, "valid": 0, "seats": 0}
    for table in tables:
        code = AREAS[table.constituency][year]
        figures = table.figures
        seats = figures["seats"]
        rounds = len(table.total)
        for field in sums:
            sums[field] += figures[field]
        for count in range(rounds):
            running = sum(c.votes[count] for c in table.candidates) + table.exhausted[count]
            if running != figures["valid"] or table.total[count] != figures["valid"]:
                raise ValueError(
                    f"{year} {table.constituency}: count {count + 1} sums to {running:,}, not the valid poll"
                )
        # the printed elected list must name the candidates with the most votes at the last count
        ranked = sorted(table.candidates, key=lambda c: -c.votes[-1])
        elected = ranked[:seats]
        listed = ep_text.fold(table.listed)
        for candidate in elected:
            surname = ep_text.fold(candidate.label.split(",")[0]).removeprefix("mc")
            if surname not in listed:
                raise ValueError(f"{year} {table.constituency}: {candidate.label} is not in the printed elected list")
        if ranked[seats].votes[-1] >= ranked[seats - 1].votes[-1] and ranked[seats].votes[-1] > 0:
            raise ValueError(f"{year} {table.constituency}: the elected set is not clear at the last count")
        contest = key("contest", day, code)
        title = TITLES[code]
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            district_area_id=area(code),
            magnitude=seats,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} European Parliament election in Ireland, {title}", "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source(),
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=VOLUME_NOTE, lang="en"
        )
        if table.derived:
            p.add(
                "note",
                id=key("note", result_set, "derived"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    f"The volume leaves the non-transferable figure of count {table.derived[0]} blank, and the "
                    "archive stores it as the valid poll minus the candidates' running totals at that count."
                ),
                lang="en",
            )
        for measure, amount, flag in (
            (REGISTERED, figures["electorate"], 0),
            (INVALID, figures["invalid"], 0),
            (VALID, figures["valid"], 0),
            (BALLOTS, figures["valid"] + figures["invalid"], DERIVED),
            (QUOTA, figures["quota"], 0),
        ):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit(code),
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=flag,
                value=amount,
            )
            if measure != BALLOTS:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=amount)
        for count in range(rounds):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit(code),
                option_id=None,
                channel=0,
                measure=EXHAUSTED,
                count_round=count + 1,
                flags=DERIVED if count + 1 in table.derived else 0,
                value=table.exhausted[count],
            )
        rows: list[dict[str, object]] = []
        for candidate in table.candidates:
            tag = f"{day}:{code}:{ep_text.fold(candidate.label)}"
            option = key("option", tag)
            person = key("person", tag)
            member = party(candidate.party or "")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_label=candidate.label,
            )
            p.add("person", id=person, gender={"M": "male", "F": "female"}.get(candidate.gender or ""))
            p.name(person, display(candidate.label), "en", "official")
            candidacy = key("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                ballot_name=candidate.label,
                member_of_party_id=member,
                is_independent=member is None,
                is_incumbent=candidate.outgoing if year > 2004 else None,
            )
            won = candidate in elected
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if won else "lost",
            )
            for count, votes in enumerate(candidate.votes):
                rows.append(
                    {
                        "result_set_id": result_set,
                        "unit_id": unit(code),
                        "option_id": option,
                        "channel": 0,
                        "measure": VOTES,
                        "count_round": count + 1,
                        "flags": 0,
                        "value": votes,
                    }
                )
        p.frame("result_value", pl.DataFrame(rows, schema=package.FACTS["result_value"], orient="row"))
    totals = national(pages, year)
    if totals is not None:
        for field, value in totals.items():
            if field in sums and sums[field] != value:
                raise ValueError(f"{year}: constituencies sum to {sums[field]:,} {field}, the volume prints {value:,}")
    return p


def report() -> dict[str, object]:
    """National totals of each election against the volume's own, for the run report."""
    out: dict[str, object] = {}
    for year, (_, url, _) in VOLUMES.items():
        pages = ep_text.layout(raw.cached(SOURCE, url).path)
        tables = ep_text.tables(pages)
        sums = {f: sum(t.figures[f] for t in tables) for f in ("electorate", "invalid", "valid", "seats")}
        out[str(year)] = {"constituencies": sums, "printed": national(pages, year)}
    return out
