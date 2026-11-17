"""European Parliament elections in Ireland, 2004 to 2019, from the Department of Housing's results volumes.

Each election date is one package, `ie.housing/ep-<year>`, built on `ie.housing/registry` (the source and the
parties). The archive already holds each election, its constituency contests, and their reporting units, so a package
adds one official, candidate-level result set to each contest and references the rest by the ids the archive gave
them. The volumes print each candidate's running total after every count. The parser reads
and checks every count, and the package stores the first count, the first preferences, under count round 1, with
the declared outcome of the full count.
"""

import re
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.ie import ep_text

SOURCE = "ie.housing"
PORTAL = "https://opendata.housing.gov.ie"
# Ireland's record predates derived country ids, so the archive keeps the id it was first given
IRELAND = "01a13bd3-643c-74a1-9302-0a8df9ef9df8"
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
# The archive already holds each election, its constituency contests, and one reporting unit per constituency
# name, keyed by the codes below; the packages add the Department's candidate-level result set to each contest.
AREAS = {
    "DUBLIN": "dublin",
    "EAST": "east",
    "NORTH-WEST": "north-west",
    "SOUTH": "south",
    "MIDLANDS - NORTH - WEST": "midlands-north-west",
}
# The 2024 load minted these three constituencies' reporting units before ids were derived from natural keys, so
# the archive keeps the ids it first gave them; the other constituencies' units carry derived ids.
MINTED = {
    "dublin": "01a13bd3-648e-728b-8d37-9849fb47d507",
    "midlands-north-west": "01a13bd3-6496-7622-8f3c-84d47e59f81f",
    "south": "01a13bd3-6497-76a0-b396-ff99eb6181e2",
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
VOLUME_NOTE = (
    "Each candidate's votes are the first preferences, the first count as the volume prints it, stored as count 1. "
    "Who was elected follows the volume's full count. The ballots cast are the valid poll "
    "plus the invalid ballot papers. The volume prints the electorate, the invalid ballot papers, the valid poll, "
    "the seats, and the quota for each constituency."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source() -> str:
    return key("source", "dhlgh-opendata")


def existing(cache: str, value: str) -> str:
    """The id the archive gave an entity it minted from a natural key, as `Changeset` derives it."""
    return str(uuid5(package.NAMESPACE, f"{cache}\x1f{value}"))


def election_id(year: int) -> str:
    return existing("external_id:eu.election", f"IE:{year}:european-parliament")


def contest_id(year: int, code: str) -> str:
    return existing("external_id:eu.contest", f"IE:{year}:{SCHEME}:{code}")


def unit(code: str) -> str:
    return MINTED.get(code) or existing(f"unit:{SCHEME}@None", code)


def party(label: str) -> str | None:
    code = re.sub(r"[^A-Z0-9]", "", label.upper())
    return None if code in ("", "NONP") else key("party", code)


def fetch() -> None:
    for _, url, _ in VOLUMES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/ep-{year}" for year in VOLUMES)]


def parse() -> None:
    registry().write()
    for year in VOLUMES:
        ep(year).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Department of Housing's open data portal as a source, the parties that stood in the 2004 to 2019 "
        "European Parliament elections in Ireland, and their electoral system",
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
        f"The {year} European Parliament election in Ireland, first preferences in each constituency",
        depends=[f"{SOURCE}/registry"],
    )
    p.uses(artifact)
    election = election_id(year)
    p.external.add(election)
    sums = {"electorate": 0, "invalid": 0, "valid": 0, "seats": 0}
    for table in tables:
        code = AREAS[table.constituency]
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
        contest = contest_id(year, code)
        p.external |= {contest, unit(code)}
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
            # the contest's default official set is the archive's earlier party-level one; moving the default is a
            # curated edit, not something a package may do to another pipeline's row
            is_default=False,
            completeness=1.0,
        )
        p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=VOLUME_NOTE, lang="en"
        )
        if 1 in table.derived:
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
        # first preferences only: every count is checked above, but the archive's fact key holds one value per
        # unit, option, and measure in a package, so later counts wait for a flow-aware package format
        if table.exhausted[0]:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit(code),
                option_id=None,
                channel=0,
                measure=EXHAUSTED,
                count_round=1,
                flags=DERIVED if 1 in table.derived else 0,
                value=table.exhausted[0],
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
            rows.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit(code),
                    "option_id": option,
                    "channel": 0,
                    "measure": VOTES,
                    "count_round": 1,
                    "flags": 0,
                    "value": candidate.votes[0],
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
