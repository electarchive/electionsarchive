import html
import re
import subprocess
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.pr_legislature"
VERSION = "1"
PUBLISHER = "Puerto Rico State Elections Commission"
TITLE = "General election official count (escrutinio) for the Legislative Assembly by district, 1992-2020"
SITE = "https://electionspuertorico.org/cgi-bin/legislatura.cgi"
YEARS = (1992, 1996, 2000, 2004, 2008, 2012, 2016, 2020)
DAYS = {
    1992: date(1992, 11, 3),
    1996: date(1996, 11, 5),
    2000: date(2000, 11, 7),
    2004: date(2004, 11, 2),
    2008: date(2008, 11, 4),
    2012: date(2012, 11, 6),
    2016: date(2016, 11, 8),
    2020: date(2020, 11, 3),
}
PAGES = [("s", "pr"), *(("s", f"{n:02}") for n in range(1, 9)), ("r", "pr"), *(("r", f"{n:02}") for n in range(1, 41))]
PARTIES = {
    "PNP": "PR:new-progressive-party",
    "PPD": "PR:popular-democratic-party",
    "PIP": "PR:puerto-rican-independence-party",
    "MVC": "PR:citizens-victory-movement",
    "PD": "PR:dignity-project",
    "PPT": "PR:party-of-the-working-people",
    "PPR": "PR:puerto-ricans-for-puerto-rico-party",
    "MUS": "PR:sovereignty-union-movement",
    "Ind.": None,
    "MIRE": None,
    "PLUS": None,
    "Partido Alternativa Ciudadana": None,
    "Partido Acción Civil": None,
}
SENATORS = {"pr": 11, **{f"{n:02}": 2 for n in range(1, 9)}}
REPRESENTATIVES = {"pr": 11, **{f"{n:02}": 1 for n in range(1, 41)}}
SENATE_SEATS = 27
HOUSE_SEATS = 51


def url(chamber: str, district: str) -> str:
    return f"{SITE}?voto=d{chamber}{district}&" + "&".join(f"{year}=on" for year in YEARS)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn").strip()


def fetch() -> None:
    for chamber, district in PAGES:
        try:
            raw.cached(SOURCE, url(chamber, district))
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfL", "--compressed", "-A", raw.USER_AGENT, url(chamber, district)],
                capture_output=True,
                check=True,
            ).stdout
            raw.store(
                SOURCE,
                url(chamber, district),
                body,
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type="text/html",
            )


def read(chamber: str, district: str) -> dict[int, list[tuple[str, str, str | None, int]]]:
    text = raw.cached(SOURCE, url(chamber, district)).path.read_text(encoding="latin-1")
    text = text.split("Los resultados de las elecciones generales")[0]
    tables: dict[int, list[tuple[str, str, str | None, int]]] = {}
    year = None
    for row in re.findall(r"<TR.*?</TR>", text, re.DOTALL | re.IGNORECASE):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()
            for cell in re.findall(r"<T[DH][^>]*>(.*?)</T[DH]>", row, re.DOTALL | re.IGNORECASE)
        ]
        cells = [cell for cell in cells if cell]
        if len(cells) == 1 and (match := re.fullmatch(r"Elecciones Generales de (\d{4})", cells[0])):
            year = int(match[1])
            continue
        if year is None or not cells or cells[0] == "Candidato":
            continue
        mark = cells.pop(0) if re.fullmatch(r"\d+|\+", cells[0]) else ""
        if len(cells) < 2 or not re.fullmatch(r"[\d,]+", cells[1]):
            continue
        label, votes = cells[0], int(cells[1].replace(",", ""))
        tables.setdefault(year, [])
        if label in ("Total", "Válidos", "Mal Votado", "No Votado", "Blanco", "Nulo"):
            continue
        if label == "Otros":
            tables[year].append(("", "Otros", None, votes))
            continue
        match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", label)
        if match is None or match[2] not in PARTIES:
            raise ValueError(f"d{chamber}{district} {year}: {label}")
        tables[year].append((mark, re.sub(r"\s+", " ", match[1]), match[2], votes))
    if set(tables) - set(YEARS):
        raise ValueError(f"d{chamber}{district}: years {sorted(tables)}")
    return tables


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico Senate and House of Representatives general elections 1992-2020 by district and at large",
            polity="US-PR",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        polity = cs.polity("US-PR")
        nation = cs.polity("US")
        island_area = cs.area("us.state", "72")
        island_unit = cs.unit("us.state", "72")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license="Public record",
        )
        for chamber, district in PAGES:
            cs.artifact(raw.cached(SOURCE, url(chamber, district)), source)
        for scheme, noun in (("us.sldu", "state senate district"), ("us.sldl", "state house district")):
            cs.add("scheme", code=scheme, polity_id=nation, kind="electoral_district", rank=2, noun=noun)

        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        systems["sntv"] = cs.key("us.electoral_system", "PR:at-large")
        if systems["sntv"] in cs.minted:
            cs.add(
                "electoral_system",
                id=systems["sntv"],
                polity_id=polity,
                rules={"formula": "sntv"},
                rules_version=1,
            )
            name(systems["sntv"], "Puerto Rico at-large single non-transferable vote")

        bodies = {}
        for chamber, key, kind, text, seats in (
            ("s", "PR:senate", "upper_house", "Senate of Puerto Rico", SENATE_SEATS),
            ("r", "PR:house", "lower_house", "Puerto Rico House of Representatives", HOUSE_SEATS),
        ):
            bodies[chamber] = (cs.key("us.body", key), text, key.split(":")[1])
            if bodies[chamber][0] in cs.minted:
                cs.add("body", id=bodies[chamber][0], polity_id=polity, kind=kind)
                name(bodies[chamber][0], text)

        districts = {}
        for chamber, district in PAGES:
            if district == "pr":
                districts[(chamber, district)] = (island_area, island_unit, "at large")
                continue
            scheme = "us.sldu" if chamber == "s" else "us.sldl"
            code = f"PR:{int(district)}"
            area = cs.area(scheme, code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=island_area, valid_from=None)
            name(area, f"{bodies[chamber][1]}, District {int(district)}")
            unit = cs.unit(scheme, code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                )
            districts[(chamber, district)] = (area, unit, f"District {int(district)}")

        persons: dict[str, UUID] = {}
        elected_total: dict[tuple[str, int], int] = {}
        for chamber, district in PAGES:
            body, body_name, body_slug = bodies[chamber]
            area, unit, shown = districts[(chamber, district)]
            at_large = district == "pr"
            for year, lines in sorted(read(chamber, district).items()):
                day = DAYS[year]
                where = "at-large" if at_large else str(int(district))
                contest_key = f"{day}:PR:{body_slug}:{where}:g"
                contest = cs.key("us.contest", contest_key)
                regular = sum(1 for line in lines if line[0].isdigit())
                added = sum(1 for line in lines if line[0] == "+")
                magnitude = (SENATORS if chamber == "s" else REPRESENTATIVES)[district]
                if regular != magnitude:
                    raise ValueError(f"{contest_key}: {regular} elected")
                elected_total[(chamber, year)] = elected_total.get((chamber, year), 0) + regular + added
                cs.add(
                    "contest",
                    id=contest,
                    election_id=cs.key("us.election", f"{day}:PR"),
                    kind="body_seats",
                    body_id=body,
                    district_area_id=area,
                    magnitude=magnitude,
                    term="full",
                    round_number=1,
                    previous_round_id=None,
                    electoral_system_id=systems["sntv" if at_large else "block" if chamber == "s" else "fptp"],
                    default_result_kind="counted",
                )
                name(contest, f"{year} {body_name} election, {shown}")
                if contest in cs.minted:
                    cs.add("slug", path=f"us/pr/{day}-general/state-{body_slug}-{where}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=cs.key("us.election", f"{day}:PR"),
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                if at_large or added:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="methodology",
                        body_md=" ".join(
                            text
                            for text, shown_if in (
                                (
                                    f"Each voter marks one at-large candidate, and the {magnitude} with the most votes are elected.",
                                    at_large,
                                ),
                                (
                                    f"{added} more {'candidate was' if added == 1 else 'candidates were'} declared elected to additional seats, which the Constitution (Article III, Section 7) gives minority parties when one party wins more than two thirds of a chamber.",
                                    added,
                                ),
                            )
                            if shown_if
                        ),
                    )
                for mark, label, party, votes in lines:
                    if party is None:
                        option = cs.key("us.option", f"{contest_key}:scattering")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="write_in",
                            party_id=None,
                            ballot_label="Scattered write-ins",
                        )
                    else:
                        token = slug(fold(label))
                        option = cs.key("us.option", f"{contest_key}:{token}")
                        party_key = PARTIES[party]
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=cs.key("us.party", party_key) if party_key else None,
                            ballot_label=label,
                        )
                        if token not in persons:
                            persons[token] = cs.key(f"{SOURCE}.person", token)
                            if persons[token] in cs.minted:
                                cs.add("person", id=persons[token])
                                name(persons[token], label)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                            person_id=persons[token],
                            option_id=option,
                            role="head",
                            ballot_name=label,
                            is_independent=party_key is None,
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
                    if mark:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        for (chamber, year), count in sorted(elected_total.items()):
            floor = SENATE_SEATS if chamber == "s" else HOUSE_SEATS
            if count < floor:
                raise ValueError(f"{chamber} {year}: {count} elected")
            print(chamber, year, count)
        return cs.write()
