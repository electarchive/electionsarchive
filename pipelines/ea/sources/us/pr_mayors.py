import csv
import html
import re
import subprocess
import tempfile
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
URL88 = (
    "https://ww2.ceepur.org/sites/ComisionEE/es-pr/Informe%20Estadstico/ESTADISTICAS%20DE%20LAS%20ELECCIONES%201988.pdf"
)
TITLE88 = "Statistics of the 1988 General Elections (Estadísticas de las Elecciones Generales de 1988), votes for mayors by precinct"
PAGES88 = (110, 116)
FIXES88 = {
    "JOSE £. TRAVERSO CACERES": "José E. Traverso Cáceres",
    "ANGEL 0. BERRIOS": "Ángel O. Berríos",
    "ANGEL S$ CRUZ RIVERA": "Ángel S. Cruz Rivera",
    "VICTOR A PEREZ VALENTIN ;": "Víctor A. Pérez Valentín",
    "RAMON E£. RIVERA (BOYE)": "Ramón E. Rivera (Boye)",
    "ANGEL _L. BULERIN RAMOS": "Ángel L. Bulerín Ramos",
}
WRITE_INS88 = {"toa alta": (("Papin Ortiz", 1601), ("Peter Albino", 1222))}
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
ESCRUTINIO = {
    2016: "https://elecciones2016.ceepur.org/Escrutinio_General_77/",
    2020: "https://elecciones2020.ceepur.org/Escrutinio_General_93/",
    2024: "https://elecciones2024.ceepur.org/Escrutinio_General_121/",
}
LEGISLATURE_INDEX = "data/LEGISLADORES_MUNICIPALES_Municipios.xml"
SEATS = {4: 5, 9: 12, 11: 14, 13: 16, 14: 17}


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
    if not raw_has(URL88):
        raw.store(
            SOURCE, URL88, curl(URL88), suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
        )
    fetch_legislatures()
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


def fetch_legislatures() -> None:
    for year in ESCRUTINIO:
        fetch_legislature(year, LEGISLATURE_INDEX)
        for page in legislature_pages(year):
            fetch_legislature(year, page)


def fetch_legislature(year: int, page: str) -> None:
    link = ESCRUTINIO[year] + page
    if raw_has(link):
        return
    if year == 2024:
        raise LookupError(f"{link}: the 2024 site refuses scripts; store it from a headless browser")
    body = subprocess.run(
        ["curl", "-sfLk", "--compressed", "-A", raw.USER_AGENT, link], capture_output=True, check=True
    ).stdout
    raw.store(SOURCE, link, body, suffix=".xml", retrieved_at=datetime.now(UTC), media_type="text/xml")


def legislature_pages(year: int) -> list[str]:
    link = ESCRUTINIO[year] + LEGISLATURE_INDEX
    if not raw_has(link):
        return []
    text = raw.cached(SOURCE, link).path.read_text(encoding="latin-1")
    pages = sorted(set(re.findall(r"<link><es>(LEGISLADORES_MUNICIPALES_[^<]+\.xml)</es>", text)))
    if len(pages) != 78:
        raise ValueError(f"{year}: {len(pages)} municipal legislature pages")
    return [f"data/{page}" for page in pages]


def read_legislature(year: int, page: str) -> tuple[str, list[tuple[str, str, int]], int]:
    text = raw.cached(SOURCE, ESCRUTINIO[year] + page).path.read_text(encoding="latin-1")
    place = re.search(r"<demarcation><es>(.*?)</es>", text)
    report = re.search(r"<report>.*?<qty>(\d+)</qty><total>(\d+)</total>", text, re.DOTALL)
    write_ins = re.search(r"<desc><es>NOMINACI.N DIRECTA</es>.*?<qty>(\d+)</qty>", text, re.DOTALL)
    if place is None or report is None or write_ins is None:
        raise ValueError(f"{year} {page}: unexpected layout")
    if report[1] != report[2]:
        raise ValueError(f"{year} {page}: {report[1]} of {report[2]} polling places reported")
    lines = []
    for option in re.findall(r"<option>(.*?)</option>", text, re.DOTALL):
        label = re.search(r"<name><es>(.*?)</es>", option)
        party = re.search(r"<peini><es>(.*?)</es>", option)
        votes = re.search(r"<votes>(\d+)</votes>", option)
        if label is None or party is None or votes is None:
            raise ValueError(f"{year} {page}: unexpected option {option[:80]}")
        code = "Ind." if party[1] == "INDPT" else party[1]
        if code not in PARTIES:
            raise ValueError(f"{year} {page}: party {party[1]}")
        lines.append((re.sub(r"\s+", " ", html.unescape(label[1])).strip(), code, int(votes[1])))
    return html.unescape(place[1]).strip(), lines, int(write_ins[1])


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


def ocr88() -> list[list[tuple[str, str, int]]]:
    party = r"PNP|PPD|PIP|IND"
    line_pattern = re.compile(rf"^(.+?)\s+({party})\s+([\d,\s.:;“\"'|]+)$")
    total_pattern = re.compile(rf"^({party})\s+([\d,\s]+)$")
    blocks: list[list[list]] = []
    current: list[list] | None = None
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            ["pdftoppm", "-r", "200", "-f", str(PAGES88[0]), "-l", str(PAGES88[1]), "-gray", "-png"]
            + [str(raw.cached(SOURCE, URL88).path), f"{folder}/p"],
            check=True,
        )
        for image in sorted(Path(folder).glob("p-*.png")):
            text = subprocess.run(
                ["tesseract", str(image), "-", "--psm", "6"], capture_output=True, check=True, text=True
            ).stdout
            for line in text.splitlines():
                line = line.strip()
                if match := line_pattern.match(line):
                    numbers = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", match[3])]
                    if current is None:
                        current = []
                        blocks.append(current)
                    current.append([match[1], match[2], numbers])
                elif (match := total_pattern.match(line)) and current:
                    for row in current:
                        if row[1] == match[1]:
                            row[2] += [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", match[2])]
                elif not line.startswith("SAN JUAN 5"):
                    current = None
    for block in blocks:
        for label, _, numbers in block:
            if len(numbers) < 2 or sum(numbers[:-1]) != numbers[-1]:
                raise ValueError(f"1988 mayor line {label}: precinct votes {numbers} do not sum to the total")
    return [[(label, party_code, numbers[-1]) for label, party_code, numbers in block] for block in blocks]


def read88(places: dict[str, str]) -> dict[str, list[tuple[str, str | None, int]]]:
    blocks = ocr88()
    names = sorted(places.values(), key=fold)
    if len(blocks) != len(names):
        raise ValueError(f"{len(blocks)} 1988 mayor blocks for {len(names)} municipios")
    found: dict[str, list[tuple[str, str | None, int]]] = {}
    for place, block in zip(names, blocks, strict=True):
        lines: list[tuple[str, str | None, int]] = []
        for index, (label, party, votes) in enumerate(block):
            if index == 1:
                label = label[len(place) :].lstrip(" =—-")
            label = FIXES88.get(label) or label.title()
            lines.append((label, "Ind." if party == "IND" else party, votes))
        for label, votes in WRITE_INS88.get(fold(place), ()):
            lines.append((label, "Write-in", votes))
        found[place] = lines
    return found


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
            summary="Puerto Rico mayoral elections 1976-2024 in all 78 municipios, 2024 by voting center, and municipal legislatures 2016-2024 by municipio",
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
        source88 = cs.source(PUBLISHER, TITLE88)
        cs.add(
            "source",
            id=source88,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE88,
            url=URL88,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL88), source88)
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
        lines88 = read88(places)
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
            if 1988 in tables:
                raise ValueError(f"{place}: the archive pages now print 1988")
            tables[1988] = [(label, party, [(unit, votes)]) for label, party, votes in lines88[place]]
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
                    source_id={1988: source88, 2024: source24}.get(year, source),
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
        limited = cs.key("us.electoral_system", "PR:municipal-legislature")
        if limited in cs.minted:
            ((territory,),) = conn.execute(
                "select polity_id from election where id = %s", [cs.key("us.election", f"{DAYS[2024]}:PR")]
            ).fetchall()
            cs.add("electoral_system", id=limited, polity_id=territory, rules={"formula": "limited"}, rules_version=1)
            name(limited, "Puerto Rico municipal legislature limited vote")
        bodies: set[UUID] = set()
        for year, base in ESCRUTINIO.items():
            day = DAYS[year]
            title = f"{year} General Election official count (escrutinio), municipal legislators by municipio"
            source_leg = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source_leg,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=base + "index.html",
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, base + LEGISLATURE_INDEX), source_leg)
            seen: set[str] = set()
            for page in legislature_pages(year):
                cs.artifact(raw.cached(SOURCE, base + page), source_leg)
                place, lines, write_ins = read_legislature(year, page)
                key = fold(place)
                if key not in held or key in seen:
                    raise ValueError(f"{year}: municipio {place}")
                seen.add(key)
                fips, polity, area, unit, path = held[key]
                cap = max(sum(1 for line in lines if line[1] == party) for party in {line[1] for line in lines})
                body = cs.key("us.body", f"PR:{fips}:legislature")
                if body in cs.minted and body not in bodies:
                    bodies.add(body)
                    cs.add("body", id=body, polity_id=polity, kind="council")
                    name(body, f"Municipal Legislature of {place}")
                contest_key = f"{day}:PR:{fips}:legislature"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=cs.key("us.election", f"{day}:PR:{fips}"),
                    kind="body_seats",
                    office_id=None,
                    body_id=body,
                    district_area_id=area,
                    magnitude=SEATS[cap],
                    term="full",
                    round_number=1,
                    previous_round_id=None,
                    electoral_system_id=limited,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {place} municipal legislature election")
                if contest in cs.minted:
                    cs.add("slug", path=f"{path}/{day}-general/municipal-legislature", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=cs.key("us.election", f"{day}:PR:{fips}"),
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source_leg,
                    is_default=True,
                    completeness=1,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=f"Votes by municipio from the State Elections Commission's official count. Each party may nominate and elect at most {cap} of the {SEATS[cap]} members; the remaining seats go to minority parties. The count does not mark which candidates were elected, so no outcomes are recorded.",
                )
                counts["contests"] += 1
                for label, party, votes in [("", "Write-in", write_ins), *lines]:
                    if party == "Write-in":
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
                        person = cs.key(f"{SOURCE}.person", f"{contest_key}:{token}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, label)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=label,
                            is_independent=party == "Ind.",
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
                    counts["values"] += 1
            if len(seen) != 78:
                raise ValueError(f"{year}: {len(seen)} municipal legislatures")
        print(counts)
        return cs.write()
