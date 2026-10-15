import html
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.idp"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = (
    "https://web.archive.org/web/20200418120020id_/"
    "https://results.thecaucuses.org/?utm_medium=social&utm_source=facebook_Forest_City_Summit"
)
PUBLISHER = "Iowa Democratic Party"
TITLE = "IDP Caucus 2020 Results"
CAUCUS = date(2020, 2, 3)
MEASURES = (17, 18, 12)
PERSONS = {
    "Bennet": ("fec.candidate", "S0CO00211"),
    "Biden": ("us.person", "joseph-r-biden-jr"),
    "Bloomberg": ("fec.candidate", "P80004740"),
    "Buttigieg": ("fec.candidate", "P00010298"),
    "Delaney": ("fec.candidate", "H2MD06195"),
    "Gabbard": ("fec.candidate", "P00009183"),
    "Klobuchar": ("fec.candidate", "S6MN00267"),
    "Sanders": ("us.person", "bernie-sanders"),
    "Warren": ("us.person", "elizabeth-warren"),
    "Yang": ("fec.candidate", "P00006486"),
}
SOURCE_NOTE = (
    "The results site is offline. This is the Internet Archive capture from April 18, 2020, taken after the "
    "recanvass. It covers all 1,765 precincts."
)
METHODOLOGY = (
    "The Iowa Democratic Party reported three figures for each precinct: the first alignment, the final alignment "
    "after supporters of nonviable groups realigned, and state delegate equivalents (SDEs). The party declared the "
    "winner by SDEs, not by raw votes. Precinct SDEs are published to four decimal places and statewide totals to "
    "three. National delegates are taken from the party's statewide table. Satellite caucuses held outside Iowa have "
    "no location on the map. Satellites inside Iowa are placed within their congressional district."
)


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".html")


def parse() -> None:
    page = raw.cached(SOURCE, ARCHIVE).path.read_text(encoding="utf-8")
    thead = re.search(r'<ul class="thead">(.*?)</ul>', page)
    if thead is None:
        raise ValueError("the page has no results header")
    labels = [label for label in re.findall(r"<li>(.*?)</li>", thead[1]) if label][2:]
    statewide = {
        label: (Decimal(sde), int(delegates))
        for label, sde, delegates in re.findall(
            r"<tr[^>]*><td[^>]*>(\w+)</td><td[^>]*>([\d.]+)</td><td[^>]*>(\d+)</td></tr>", page
        )
    }
    if list(statewide) != labels:
        raise ValueError(f"the statewide table lists {list(statewide)}, the precinct table {labels}")
    rows: list[tuple[str, str, str, int, Decimal]] = []
    for block in page.split('<div class="precinct-rows ')[1:]:
        county = html.unescape(block[: block.index(" is-visible")])
        total = re.search(r'<ul class="total-row">(.*?)</ul>', block)
        if total is None:
            raise ValueError(f"{county} has no total row")
        counted = [Decimal(0)] * (3 * len(labels))
        for line in re.findall(r"<ul>(.*?)</ul>", block):
            precinct, *cells = (html.unescape(cell) for cell in re.findall(r"<li>(.*?)</li>", line))
            if len(cells) != len(counted):
                raise ValueError(f"{county} {precinct} has {len(cells)} cells")
            for index, cell in enumerate(cells):
                counted[index] += Decimal(cell)
                rows.append((county, precinct, labels[index // 3], MEASURES[index % 3], Decimal(cell)))
        printed = [Decimal(cell.replace(",", "")) for cell in re.findall(r"<li>(.*?)</li>", total[1])[1:]]
        if printed != counted:
            raise ValueError(f"{county}: precincts sum to {counted}, the total row prints {printed}")
    frame = pl.DataFrame(
        rows,
        schema={
            "county": pl.String,
            "precinct": pl.String,
            "label": pl.String,
            "measure": pl.Int16,
            "value": pl.Decimal(12, 4),
        },
        orient="row",
    )
    if frame.select("county", "precinct").unique().height * 3 * len(labels) != frame.height:
        raise ValueError("a precinct repeats within its county")
    sdes = dict(frame.filter(pl.col("measure") == 12).group_by("label").agg(pl.col("value").sum()).iter_rows())
    for label, (sde, _) in statewide.items():
        if round(sdes[label], 3) != sde:
            raise ValueError(f"{label}: precinct SDEs sum to {sdes[label]}, the statewide table prints {sde}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    pl.DataFrame(
        [(label, sde, delegates) for label, (sde, delegates) in statewide.items()],
        schema={"label": pl.String, "sde": pl.Decimal(12, 3), "delegates": pl.Int64},
        orient="row",
    ).write_parquet(PARSED / "statewide.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    statewide = pl.read_parquet(PARSED / "statewide.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's 2020 Democratic caucuses by precinct: alignments, state delegate equivalents, and delegates",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.thecaucuses.org/",
            license=None,
            notes=SOURCE_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, ARCHIVE), source)

        democrats = cs.key("us.party", "democratic-party")
        president = cs.key("us.office", "president")
        persons = {label: cs.key(scheme, value) for label, (scheme, value) in PERSONS.items()}
        if cs.minted & {democrats, president, *persons.values()}:
            raise ValueError("the Democratic Party, the presidency, and the linked candidates must already exist")
        for label, text in (("Patrick", "Deval Patrick"), ("Steyer", "Tom Steyer")):
            persons[label] = cs.key(f"{SOURCE}.person", slug(text))
            cs.add("person", id=persons[label])
            name(persons[label], text)

        iowa = cs.polity("US-IA")
        state = cs.area("us.state", "19")
        rules = cs.key("us.electoral_system", "IA:democratic-caucus")
        cs.add(
            "electoral_system",
            id=rules,
            polity_id=iowa,
            rules={"formula": "caucus", "viability": 0.15, "alignments": 2},
            rules_version=1,
        )
        name(rules, "Iowa Democratic caucus rules")

        nomination = cs.key("us.election", f"{CAUCUS}:democratic-presidential-primaries")
        cs.add(
            "election",
            id=nomination,
            polity_id=cs.polity("US"),
            kind="primary",
            date_from=CAUCUS,
            date_to=date(2020, 8, 11),
            status="held",
        )
        name(nomination, "2020 Democratic Party presidential primaries")
        cs.add("slug", path=f"us/{CAUCUS}-democratic-presidential-primaries", entity_id=nomination)
        national = cs.key("us.contest", f"{CAUCUS}:president:democratic")
        cs.add(
            "contest",
            id=national,
            election_id=nomination,
            kind="delegate_allocation",
            office_id=president,
            party_scope_id=democrats,
            magnitude=3979,
            round_number=1,
            default_result_kind="counted",
        )
        name(national, "2020 Democratic presidential nomination")
        cs.add("slug", path=f"us/{CAUCUS}-democratic-presidential-primaries/president", entity_id=national)

        caucuses = cs.key("us.election", f"{CAUCUS}:IA:democratic-caucuses")
        cs.add(
            "election",
            id=caucuses,
            polity_id=iowa,
            parent_id=nomination,
            kind="caucus",
            date_from=CAUCUS,
            date_to=CAUCUS,
            status="held",
        )
        name(caucuses, "2020 Iowa Democratic caucuses")
        cs.add("slug", path=f"us/ia/{CAUCUS}-democratic-caucuses", entity_id=caucuses)
        contest_key = f"{CAUCUS}:IA:president:democratic"
        contest = cs.key("us.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=caucuses,
            kind="delegate_allocation",
            office_id=president,
            party_scope_id=democrats,
            district_area_id=state,
            magnitude=int(statewide["delegates"].sum()),
            round_number=1,
            allocation_parent_id=national,
            electoral_system_id=rules,
            default_result_kind="counted",
        )
        name(contest, "2020 Iowa Democratic caucuses")
        cs.add("slug", path=f"us/ia/{CAUCUS}-democratic-caucuses/president", entity_id=contest)

        options: dict[str, UUID] = {}
        for label in statewide["label"]:
            kind = {"Other": "others", "Uncommitted": "uncommitted"}.get(label, "candidate")
            option_key = f"{contest_key}:{persons[label] if kind == 'candidate' else kind}"
            options[label] = cs.key("us.option", option_key)
            cs.add(
                "contest_option",
                id=options[label],
                contest_id=contest,
                kind=kind,
                party_id=democrats if kind == "candidate" else None,
                ballot_label=label,
            )
            if kind == "candidate":
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{option_key}:head"),
                    person_id=persons[label],
                    option_id=options[label],
                    role="head",
                    ballot_name=label,
                    is_independent=False,
                )

        cs.add("scheme", code="us-ia.caucus_precinct", polity_id=iowa, kind="precinct", rank=3, noun="caucus precinct")
        counties: dict[str, tuple[str, UUID, UUID]] = {
            text: (code, area, unit)
            for text, code, area, unit in conn.execute(
                """select n.text, u.code, u.area_id, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%'"""
            ).fetchall()
        }
        units: dict[tuple[str, str], UUID] = {}
        for county, precinct in frame.select("county", "precinct").unique().sort("county", "precinct").iter_rows():
            district = re.fullmatch(r"CD(\d) Satellite", county)
            if county in counties:
                code, area, parent = counties[county]
            elif district is not None:
                code = slug(county)
                area, parent = cs.area("us.cd", f"190{district[1]}"), cs.unit("us.cd", f"190{district[1]}")
            elif county == "Out of State Satellite":
                code, area, parent = slug(county), None, cs.unit("us.state", "19")
            else:
                raise ValueError(f"{county} is neither an Iowa county nor a satellite group")
            units[(county, precinct)] = cs.unit("us-ia.caucus_precinct", f"{code}:{precinct}")
            cs.add(
                "reporting_unit",
                id=units[(county, precinct)],
                polity_id=iowa,
                scheme_code="us-ia.caucus_precinct",
                code=f"{code}:{precinct}",
                parent_id=parent,
                area_relation="unlocated" if area is None else "within",
                area_id=area,
                kind="standard",
                valid_from=CAUCUS,
                valid_to=date(2020, 2, 4),
            )

        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
        cs.add(
            "result_set",
            id=result_set,
            election_id=caucuses,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=1,
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=METHODOLOGY)
        for label, delegates in statewide.select("label", "delegates").iter_rows():
            if delegates:
                cs.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=delegates)
        if result_set in cs.minted:
            cs.frame(
                "result_value",
                frame.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.concat_str("county", pl.lit("\x1f"), "precinct").replace_strict(
                        {f"{county}\x1f{precinct}": str(unit) for (county, precinct), unit in units.items()}
                    ),
                    option_id=pl.col("label").replace_strict({label: str(id) for label, id in options.items()}),
                    channel=pl.lit(0, pl.Int16),
                    measure="measure",
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="value",
                ),
            )
        return cs.write()
