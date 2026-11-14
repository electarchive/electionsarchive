import csv
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

SOURCE = "us.pr_mayors"
VERSION = "1"
PUBLISHER = "Puerto Rico State Elections Commission"
TITLE = "General election official count (escrutinio) for mayor by municipio, 1976-2020"
SITE = "https://electionspuertorico.org/cgi-bin/municipios.cgi"
YEARS = (1976, 1980, 1984, 1988, 1992, 1996, 2000, 2004, 2008, 2012, 2016, 2020)
DAYS = {
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
    2020: date(2020, 11, 3),
    2024: date(2024, 11, 5),
}
URL24 = "https://electionspuertorico.org/datos/2024/escrutinio/municipal.csv"
CANDIDATES24 = "https://electionspuertorico.org/referencia/candidatos/2024/"
TITLE24 = "2024 General Election official count (escrutinio) by voting center, municipal ballot"
COLUMNS24 = ("PNP", "PPD", "MVC", "PIP", "PD", "IND")
PARTIES = {
    "PNP": "PR:new-progressive-party",
    "PPD": "PR:popular-democratic-party",
    "PIP": "PR:puerto-rican-independence-party",
    "PSP": "PR:puerto-rican-socialist-party",
    "PRP": "PR:puerto-rico-renewal",
    "MVC": "PR:citizens-victory-movement",
    "PD": "PR:dignity-project",
    "PPT": "PR:party-of-the-working-people",
    "PPR": "PR:puerto-ricans-for-puerto-rico-party",
    "MUS": "PR:sovereignty-union-movement",
    "Ind.": None,
    "Write-in": None,
}
SKIP = ("Total", "Válidos", "Mal Votado", "No Votado", "Blanco", "Nulo")


def url(code: str) -> str:
    return f"{SITE}?municipio={code}&" + "&".join(f"{year}=on" for year in YEARS)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn").strip()


def curl(link: str) -> bytes:
    return subprocess.run(
        ["curl", "-sfL", "--compressed", "-A", raw.USER_AGENT, link], capture_output=True, check=True
    ).stdout


def municipios() -> dict[str, str]:
    text = raw.cached(SOURCE, SITE).path.read_text(encoding="latin-1")
    select = text[text.index('<SELECT NAME="municipio"') : text.index("</SELECT>")]
    found = {
        code: html.unescape(label).strip()
        for code, label in re.findall(r'<OPTION VALUE="(\w+)"[^>]*>([^<\n]+)', select)
        if code not in ("pr", "rp")
    }
    if len(found) != 78:
        raise ValueError(f"{len(found)} municipios in the form")
    return found


def fetch() -> None:
    if not raw_has(SITE):
        raw.store(SOURCE, SITE, curl(SITE), suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
    for link, suffix, media_type in ((URL24, ".csv", "text/csv"), (CANDIDATES24, ".html", "text/html")):
        if not raw_has(link):
            raw.store(SOURCE, link, curl(link), suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media_type)
    for code in municipios():
        if not raw_has(url(code)):
            raw.store(
                SOURCE,
                url(code),
                curl(url(code)),
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type="text/html",
            )


def raw_has(link: str) -> bool:
    try:
        raw.cached(SOURCE, link)
    except LookupError:
        return False
    return True


def read(code: str, place: str) -> dict[int, list[tuple[str, str | None, int]]]:
    text = raw.cached(SOURCE, url(code)).path.read_text(encoding="latin-1")
    tables: dict[int, list[tuple[str, str | None, int]]] = {}
    totals: dict[int, int] = {}
    year = None
    inside = False
    for row in re.findall(r"<TR.*?</TR>", text, re.DOTALL | re.IGNORECASE):
        if "<TABLE" in row.upper():
            match = re.search(r"Elecciones Generales.*?de (\d{4})", row)
            if match:
                year, inside = int(match[1]), False
            continue
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()
            for cell in re.findall(r"<T[DH][^>]*>(.*?)</T[DH]>", row, re.DOTALL | re.IGNORECASE)
        ]
        cells = [cell for cell in cells if cell]
        if len(cells) == 1 and "<B>" in row.upper():
            inside = cells[0] == f"Alcalde de {place}"
            if inside and year in tables:
                raise ValueError(f"{place} {year}: two mayor tables")
            if inside and year is not None:
                tables[year] = []
                totals[year] = 0
            continue
        if not inside or year is None or len(cells) < 2 or not re.fullmatch(r"[\d,]+", cells[1]):
            continue
        label, votes = cells[0], int(cells[1].replace(",", ""))
        if label == "Total":
            if totals[year] != votes:
                raise ValueError(f"{place} {year}: lines sum to {totals[year]}, total {votes}")
            continue
        if label != "Válidos":
            totals[year] += votes
        if label in SKIP:
            continue
        if label in ("Otros", "Candidatos Independientes"):
            tables[year].append(("Otros", None, votes))
            continue
        match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", label)
        if match is None:
            raise ValueError(f"{place} {year}: {label}")
        tables[year].append((re.sub(r"\s+", " ", match[1]), match[2], votes))
    return tables


def candidates24() -> dict[str, dict[str, str]]:
    text = raw.cached(SOURCE, CANDIDATES24).path.read_text(encoding="latin-1")
    found: dict[str, dict[str, str]] = {}
    for row in re.findall(r"<TR.*?</TR>", text[text.index('NAME="MUNICIPALES"') :], re.DOTALL | re.IGNORECASE):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ")).strip()
            for cell in re.findall(r"<T[DH][^>]*>(.*?)</T[DH]>", row, re.DOTALL | re.IGNORECASE)
        ]
        if len(cells) != 7 or not cells[0] or cells[0].startswith("Municipio"):
            continue
        found[fold(cells[0])] = {party: label for party, label in zip(COLUMNS24, cells[1:], strict=True) if label}
        if len(found) == 78:
            return found
    raise ValueError(f"{len(found)} municipios in the 2024 candidate list")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico mayoral elections 1976-2024 in all 78 municipios, 2024 by voting center",
            polity="US-PR",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

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
        cs.artifact(raw.cached(SOURCE, SITE), source)
        source24 = cs.source(PUBLISHER, TITLE24)
        cs.add(
            "source",
            id=source24,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE24,
            url="https://electionspuertorico.org/datos/2024/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL24), source24)
        cs.artifact(raw.cached(SOURCE, CANDIDATES24), source24)
        system = cs.key("us.electoral_system", "fptp")
        if system in cs.minted:
            raise ValueError("the plurality system must already exist")
        held = {
            fold(text): (fips, polity, area, unit, path)
            for fips, polity, area, unit, text, path in conn.execute(
                """select c.code, t.polity_id, c.area_id, u.id, n.text, s.path
                   from area_code c join area a on a.id = c.area_id and a.scheme_code = 'us.county'
                   join polity_territory t on t.area_id = c.area_id
                   join reporting_unit u on u.area_id = c.area_id and u.scheme_code = 'us.county'
                   join name n on n.entity_id = t.polity_id and n.is_preferred
                   join slug s on s.entity_id = t.polity_id and s.is_current
                   where c.code like '72%%'"""
            ).fetchall()
        }
        places = municipios()
        if len(held) != 78 or {fold(place) for place in places.values()} != held.keys():
            raise ValueError("the form's municipios do not match the held municipio polities")
        by_area = {area: key for key, (_, _, area, _, _) in held.items()}
        colegios = {
            code: (unit, by_area[area])
            for code, unit, area in conn.execute(
                """select u.code, u.id, p.area_id from reporting_unit u join reporting_unit p on p.id = u.parent_id
                   where u.scheme_code = 'us.precinct' and u.code like '2024:PR:%%'"""
            ).fetchall()
        }
        names24 = candidates24()
        if names24.keys() != held.keys():
            raise ValueError("the 2024 candidate list does not match the held municipios")
        votes24: dict[str, dict[str, list[tuple[UUID, int]]]] = {}
        for row in csv.DictReader(raw.cached(SOURCE, URL24).path.read_text(encoding="utf-8-sig").splitlines()):
            unit24, key = colegios[f"2024:PR:{int(row['PREKEY']):03}:{int(row['UNIKEY']):02}:{int(row['COLKEY']):02}"]
            for party in (*COLUMNS24, "OTR"):
                votes = int(row[f"ALC_{party}"] or 0)
                if party not in names24[key] and party != "OTR" and votes:
                    raise ValueError(f"{key}: {votes} votes for an absent {party} candidate")
                votes24.setdefault(key, {}).setdefault(party, []).append((unit24, votes))
        counts = {"contests": 0, "values": 0}
        for code, place in sorted(places.items(), key=lambda item: fold(item[1])):
            fips, polity, area, unit, path = held[fold(place)]
            cs.artifact(raw.cached(SOURCE, url(code)), source)
            office = cs.key("us.office", f"PR:{fips}:mayor")
            if office in cs.minted:
                cs.add("office", id=office, polity_id=polity, kind="mayor")
                name(office, f"Mayor of {place}")
            persons: dict[str, UUID] = {}
            tables = {
                year: [(label, party, [(unit, votes)]) for label, party, votes in lines]
                for year, lines in read(code, place).items()
            }
            tables[2024] = [
                (names24[fold(place)][party], "Ind." if party == "IND" else party, votes24[fold(place)][party])
                for party in COLUMNS24
                if party in names24[fold(place)]
            ] + [("Otros", None, votes24[fold(place)]["OTR"])]
            for year, lines in sorted(tables.items()):
                day = DAYS[year]
                election = cs.key("us.election", f"{day}:PR:{fips}")
                if election in cs.minted:
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        parent_id=cs.key("us.election", f"{day}:PR"),
                        kind="general",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    name(election, f"{year} {place} general election")
                    cs.add("slug", path=f"{path}/{day}-general", entity_id=election)
                contest_key = f"{day}:PR:{fips}:mayor"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=area,
                    magnitude=1,
                    term="full",
                    round_number=1,
                    previous_round_id=None,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {place} mayoral election")
                if contest in cs.minted:
                    cs.add("slug", path=f"{path}/{day}-general/mayor", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source24 if year == 2024 else source,
                    is_default=True,
                    completeness=1,
                )
                if year == 2024:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="methodology",
                        body_md="Votes by voting center from the State Elections Commission's official count of the municipal ballot. The file gives votes by party column; candidate names are matched to those columns from the ballot's candidate list. Mixed-vote and unvoted counts are not loaded.",
                    )
                counts["contests"] += 1
                for label, party, cells in lines:
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
                        party_key = PARTIES.get(party)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="write_in" if party == "Write-in" else "candidate",
                            party_id=cs.key("us.party", party_key) if party_key else None,
                            ballot_label=label if party in PARTIES else f"{label} ({party})",
                        )
                        if token not in persons:
                            persons[token] = cs.key(f"{SOURCE}.person", f"{fips}:{token}")
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
                            is_independent=party in ("Ind.", "Write-in"),
                        )
                    for cell, votes in cells:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cell,
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=votes,
                        )
                        counts["values"] += 1
        print(counts)
        return cs.write()
