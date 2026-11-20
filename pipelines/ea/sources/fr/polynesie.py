"""Elections to the Assembly of French Polynesia, by polling station, from the Haut-commissariat's results files."""

import re
import unicodedata
from datetime import date
from urllib.parse import quote
from uuid import uuid5

import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package

SOURCE = "fr.polynesie"
BASE = "https://www.polynesie-francaise.pref.gouv.fr"
SOURCE_ID = str(uuid5(package.NAMESPACE, "fr.polynesie\x1fsource"))
FRANCE = "01a13b8a-b1bc-7045-aa86-d470ebdf31b2"
TERRITORY = "01a13b8a-b1d1-71c3-af58-ab73d61eed2b"
COMMUNES = {
    "98711": "01a13b8a-b236-7126-be61-869f03702836",  # Anaa
    "98712": "01a13b8a-b236-7126-be61-86a06e6cf4ae",  # Arue
    "98713": "01a13b8a-b236-7126-be61-86a14a2394d2",  # Arutua
    "98714": "01a13b8a-b236-7126-be61-86a2648fb34a",  # Bora-Bora
    "98715": "01a13b8a-b236-7126-be61-86a37d0661c9",  # Faaa
    "98716": "01a13b8a-b236-7126-be61-86a462f79f03",  # Fakarava
    "98717": "01a13b8a-b236-7126-be61-86a5247ddf62",  # Fangatau
    "98718": "01a13b8a-b236-7126-be61-86a6bab74214",  # Fatu-Hiva
    "98719": "01a13b8a-b236-7126-be61-86a7f4cba880",  # Gambier
    "98720": "01a13b8a-b236-7126-be61-86a8483d62d0",  # Hao
    "98721": "01a13b8a-b236-7126-be61-86a9ca02776f",  # Hikueru
    "98722": "01a13b8a-b236-7126-be61-86aa0faac01c",  # Hitiaa O Te Ra
    "98723": "01a13b8a-b236-7126-be61-86ab01a351b0",  # Hiva-Oa
    "98724": "01a13b8a-b236-7126-be61-86acfb6b9c16",  # Huahine
    "98725": "01a13b8a-b236-7126-be61-86adfdfd7a5e",  # Mahina
    "98726": "01a13b8a-b236-7126-be61-86aeab5eb7e1",  # Makemo
    "98727": "01a13b8a-b236-7126-be61-86af4b1944ea",  # Manihi
    "98728": "01a13b8a-b236-7126-be61-86b0836cfe5d",  # Maupiti
    "98729": "01a13b8a-b236-7126-be61-86b19f49ec8e",  # Moorea-Maiao
    "98730": "01a13b8a-b236-7126-be61-86b2774e1a28",  # Napuka
    "98731": "01a13b8a-b236-7126-be61-86b364900777",  # Nuku-Hiva
    "98732": "01a13b8a-b236-7126-be61-86b49458f4cb",  # Nukutavake
    "98733": "01a13b8a-b236-7126-be61-86b572fb7745",  # Paea
    "98734": "01a13b8a-b236-7126-be61-86b664271f3d",  # Papara
    "98735": "01a13b8a-b236-7126-be61-86b7dc7fa11c",  # Papeete
    "98736": "01a13b8a-b236-7126-be61-86b88802e843",  # Pirae
    "98737": "01a13b8a-b236-7126-be61-86b9a8b62120",  # Pukapuka
    "98738": "01a13b8a-b236-7126-be61-86bafb6eee47",  # Punaauia
    "98739": "01a13b8a-b236-7126-be61-86bb495c157d",  # Raivavae
    "98740": "01a13b8a-b236-7126-be61-86bc1bcd2414",  # Rangiroa
    "98741": "01a13b8a-b236-7126-be61-86bd73ed2c79",  # Rapa
    "98742": "01a13b8a-b236-7126-be61-86be56f990ba",  # Reao
    "98743": "01a13b8a-b236-7126-be61-86bfb73922c6",  # Rimatara
    "98744": "01a13b8a-b236-7126-be61-86c0992649b9",  # Rurutu
    "98745": "01a13b8a-b236-7126-be61-86c11e53a195",  # Tahaa
    "98746": "01a13b8a-b236-7126-be61-86c272323e3a",  # Tahuata
    "98747": "01a13b8a-b236-7126-be61-86c3a4514c71",  # Taiarapu-Est
    "98748": "01a13b8a-b236-7126-be61-86c46bc4b8be",  # Taiarapu-Ouest
    "98749": "01a13b8a-b236-7126-be61-86c59273a494",  # Takaroa
    "98750": "01a13b8a-b236-7126-be61-86c621ab19c3",  # Taputapuatea
    "98751": "01a13b8a-b236-7126-be61-86c766d918fc",  # Tatakoto
    "98752": "01a13b8a-b236-7126-be61-86c888835ddb",  # Teva I Uta
    "98753": "01a13b8a-b236-7126-be61-86c93d0e928a",  # Tubuai
    "98754": "01a13b8a-b236-7126-be61-86caec759e8d",  # Tumaraa
    "98755": "01a13b8a-b236-7126-be61-86cb33c94c68",  # Tureia
    "98756": "01a13b8a-b236-7126-be61-86cc2b3bf205",  # Ua-Huka
    "98757": "01a13b8a-b236-7126-be61-86cdc0dfac4f",  # Ua-Pou
    "98758": "01a13b8a-b236-7126-be61-86ce725d2ff2",  # Uturoa
}
COMMUNE_NAMES = {
    "98711": "Anaa",
    "98712": "Arue",
    "98713": "Arutua",
    "98714": "Bora-Bora",
    "98715": "Faaa",
    "98716": "Fakarava",
    "98717": "Fangatau",
    "98718": "Fatu-Hiva",
    "98719": "Gambier",
    "98720": "Hao",
    "98721": "Hikueru",
    "98722": "Hitiaa O Te Ra",
    "98723": "Hiva-Oa",
    "98724": "Huahine",
    "98725": "Mahina",
    "98726": "Makemo",
    "98727": "Manihi",
    "98728": "Maupiti",
    "98729": "Moorea-Maiao",
    "98730": "Napuka",
    "98731": "Nuku-Hiva",
    "98732": "Nukutavake",
    "98733": "Paea",
    "98734": "Papara",
    "98735": "Papeete",
    "98736": "Pirae",
    "98737": "Pukapuka",
    "98738": "Punaauia",
    "98739": "Raivavae",
    "98740": "Rangiroa",
    "98741": "Rapa",
    "98742": "Reao",
    "98743": "Rimatara",
    "98744": "Rurutu",
    "98745": "Tahaa",
    "98746": "Tahuata",
    "98747": "Taiarapu-Est",
    "98748": "Taiarapu-Ouest",
    "98749": "Takaroa",
    "98750": "Taputapuatea",
    "98751": "Tatakoto",
    "98752": "Teva I Uta",
    "98753": "Tubuai",
    "98754": "Tumaraa",
    "98755": "Tureia",
    "98756": "Ua-Huka",
    "98757": "Ua-Pou",
    "98758": "Uturoa",
}
SCHEME = "pf.bureau"
SECTION_SCHEME = "pf.section"
SEATS = 57

FILES = {
    "t1_2023": (
        "/contenu/telechargement/50520/321793/file/T1 Résultats définitifs de l'élection des représentants "
        "à l'Assemblée de la Polynésie française.xlsx"
    ),
    "t2_2023": "/contenu/telechargement/50733/322945/file/Résultats définitifs_global PF.xlsx",
    "t1_2023_pdf": "/contenu/telechargement/50519/321788/file/Résultats définitifs PF_T1.pdf",
    "t2_2023_pdf": "/contenu/telechargement/50751/323027/file/01-05-2023 - Résultats définitifs Tour 2.pdf",
    "t1_2018": (
        "/contenu/telechargement/30258/157807/file/RESULTATS DEFINITIFS - 1er tour de l'élection "
        "représentants à l'APF - 22 avril 2018.xlsx"
    ),
    "t2_2018": "/contenu/telechargement/30407/158579/file/Résultats définitifs - 2nd tour élection territoriale.xlsx",
    "t2_2013_sections": (
        "/contenu/telechargement/8044/48254/file/RESULTATS DEFINITIFS - 2e tour élection territoriale - "
        "5 mai 2013 par sections+camemberts.pdf"
    ),
    "t2_2013": (
        "/contenu/telechargement/8042/48246/file/RESULTATS DEFINITIFS - 2e tour élection territoriale - "
        "5 mai 2013 par bureaux de vote.pdf"
    ),
}
DAYS = {
    2023: (date(2023, 4, 16), date(2023, 4, 30)),
    2018: (date(2018, 4, 22), date(2018, 5, 6)),
    2013: (date(2013, 4, 21), date(2013, 5, 5)),
}
# Rounds held in the archive for each year: 2013 holds the second round only.
ROUNDS = {2023: (1, 2), 2018: (1, 2), 2013: (2,)}
# Declared seats by list, from the Haut-commissariat's press release of 1 May 2023 (second round).
SEATS_DECLARED = {
    (2023, 2): {"TAVINI HUIRAATIRA NO TE AO MAOHI - FLP": 38, "TAPURA HUIRAATIRA": 16, "A HERE IA PORINETIA": 3}
}
# Declared national totals of the second round of 2023 (press release): registered, voters, blank, invalid, valid.
DECLARED_2023_2 = {2: 210385, 3: 147181, 6: 823, 5: 700, 4: 145658}
DECLARED_2023_1 = {2: 210161, 3: 126272, 4: 124375}
# One party per political movement; a list label in any year maps to it.
PARTIES = {
    "TAVINI HUIRAATIRA": "Tavini Huiraatira",
    "TAPURA HUIRAATIRA": "Tapura Huiraatira",
    "TAHOERAA HUIRAATIRA": "Tahoeraa Huiraatira",
    "A HERE IA PORINETIA": "A here ia Porinetia",
    "TE ORA API O PORINETIA": "Te Ora Api o Porinetia",
    "E REO MANAHUNE": "E reo Manahune",
    "DIGNITE BONHEUR": "Dignité Bonheur",
    "AMUITAHIRAA O TE NUNAA MAOHI": "Amuitahiraa o te Nunaa Maohi",
    "IA ORA TE NUNAA": "Ia ora te Nunaa",
    "HAU MA'OHI": "Hau Ma'ohi",
    "HEIURA LES VERTS": "Heiura Les Verts",
    "UPLD": "Union pour la démocratie",
    "A TI'A PORINETIA": "A Ti'a Porinetia",
}
METHOD = (
    "Counted by the Haut-commissariat de la République per polling station (bureau de vote), as published in its "
    "definitive results. Registered voters are the electoral roll, ballots cast are the voters, and valid votes are "
    "the votes for lists. Since 2013 the whole territory is one constituency divided into eight sections, and the "
    "sections are shown as the parent of their polling stations. Polling stations are placed in their commune."
)
SECTIONS_NOTE = (
    "Since the organic law of 2011, the 57 members are elected in a single territorial constituency from closed "
    "lists made up of eight section sublists. In the first round a list with an absolute majority of valid votes "
    "takes a bonus of one third of the seats (19); otherwise a second round is held between lists that won at least "
    "12.5% of valid votes, and lists that won at least 5% may merge into them. The list ahead in the second round "
    "takes the bonus, and the remaining seats go to the lists with at least 5% of valid votes by highest average, "
    "then are shared among the eight sections."
)


def url(name: str) -> str:
    return BASE + quote(FILES[name])


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, url(name), suffix=f".{FILES[name].rsplit('.', 1)[1]}")


def packages() -> list[str]:
    return ["fr.polynesie/registry", *(f"fr.polynesie/ap-{year}" for year in DAYS)]


def parse() -> None:
    registry().write()
    for year in DAYS:
        assembly(year).write()


def emit() -> None:
    package.run("load", packages())


def plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def party_of(label: str) -> str | None:
    text = plain(label)
    for head in PARTIES:
        if text.startswith(plain(head)):
            return head
    return None


def party_id(head: str) -> str:
    return key("party", head)


def communes() -> dict[str, str]:
    """Area ids of the communes, by plain name."""
    return {plain(name): COMMUNES[code] for code, name in COMMUNE_NAMES.items()}


def territory() -> str:
    return TERRITORY


def number(value: object) -> int:
    text = "" if value is None else str(value).strip().replace(" ", "").replace("\xa0", "")
    return int(float(text)) if text else 0


def workbook(name: str) -> tuple[list[str], list[dict[str, object]]]:
    """The list labels in ballot order and the polling-station rows of one round's workbook."""
    frame = pl.read_excel(
        raw.cached(SOURCE, url(name)).path,
        sheet_name="Export",
        engine="calamine",
        has_header=False,
        infer_schema_length=0,
    )
    rows = [list(row) for row in frame.iter_rows()]
    head = rows[0]
    columns = [i for i in range(10, len(head)) if head[i] and str(head[i]).strip()]
    labels = [str(head[i]).strip() for i in columns]
    out: list[dict[str, object]] = []
    for row in rows[2:]:
        found = re.match(r"\d+", "" if row[2] is None else str(row[2]).strip())
        if found is None:
            continue
        out.append(
            {
                "section": str(row[0]).strip(),
                "commune": str(row[1]).strip(),
                "bureau": int(found.group()),
                "registered": number(row[3]),
                "voters": number(row[4]),
                "blank": number(row[7]),
                "invalid": number(row[8]),
                "valid": number(row[9]),
                "votes": [number(row[i]) for i in columns],
            }
        )
    return labels, out


def table_2013() -> tuple[list[str], list[dict[str, object]]]:
    """Second-round 2013 polling-station rows from the Haut-commissariat's PDF table."""
    document = pymupdf.open(raw.cached(SOURCE, url("t2_2013")).path)
    labels = ["UPLD", "Tahoeraa Huiraatira", "A Ti'a Porinetia"]
    number_re = re.compile(r"\d+(?: \d{3})*(?:,\d+)?")
    out: list[dict[str, object]] = []
    for page in document:
        ordered = sorted(page.get_text("words"), key=lambda w: w[1])
        grouped: list[list[tuple[float, float, str]]] = []
        top = -100.0
        for x0, y0, x1, _, word, *_ in ordered:
            if y0 - top > 2.5:
                grouped.append([])
                top = y0
            grouped[-1].append((x0, x1, word))
        for words in grouped:
            words.sort()
            cells: list[str] = []
            last = -100.0
            for x0, x1, word in words:
                if cells and x0 - last < 4.5:
                    cells[-1] += " " + word
                else:
                    cells.append(word)
                last = x1
            if len(cells) != 15 or "section" not in cells[0].lower() or cells[1].strip().isdigit():
                continue
            if not cells[2].strip().isdigit():
                continue
            nums = [number(c.replace(",", ".")) if "," not in c else None for c in cells[3:]]
            if not all(number_re.fullmatch(c) for c in cells[3:]):
                continue
            # registered, voters, abstention, participation(%), blank and invalid, valid, then votes and % per list
            registered, voters, _, _, spoiled, valid = (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
            votes = [nums[6], nums[8], nums[10]]
            if None in (registered, voters, spoiled, valid, *votes):
                continue
            out.append(
                {
                    "section": cells[0].strip(),
                    "commune": cells[1].strip(),
                    "bureau": int(cells[2]),
                    "registered": registered,
                    "voters": voters,
                    "blank": None,
                    "invalid": None,
                    "spoiled": spoiled,
                    "valid": valid,
                    "votes": votes,
                }
            )
    return labels, out


def registry() -> Package:
    p = Package(
        "fr.polynesie/registry",
        SOURCE,
        "The Assembly of French Polynesia, its electoral systems, and the parties that ran, from the "
        "Haut-commissariat's results",
    )
    p.external |= {FRANCE}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Haut-commissariat de la République en Polynésie française",
        title="Résultats des élections territoriales",
        url=f"{BASE}/Actions-de-l-Etat/Elections/Territoriales",
        license="Licence Ouverte 2.0",
        license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        attribution="Haut-commissariat de la République en Polynésie française",
    )
    for name in FILES:
        p.artifact(raw.cached(SOURCE, url(name)), SOURCE_ID)
    p.add("scheme", code=SCHEME, polity_id=FRANCE, kind="precinct", rank=6, noun="polling station", code_system=None)
    p.add(
        "scheme",
        code=SECTION_SCHEME,
        polity_id=FRANCE,
        kind="electoral_district",
        rank=5,
        noun="section",
        code_system=None,
    )
    body = key("body", "assembly")
    p.add("body", id=body, polity_id=FRANCE, kind="unicameral")
    p.name(body, "Assembly of French Polynesia", "en", "official")
    p.name(body, "Assemblée de la Polynésie française", "fr", "native", False)
    p.add("body_size", body_id=body, seats=SEATS, valid_from=date(2013, 1, 1), valid_to=None)
    for since, until, rules, text in SYSTEMS:
        system = key("electoral_system", since.year)
        p.add(
            "electoral_system",
            id=system,
            polity_id=FRANCE,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=text, lang="en")
    for head, label in PARTIES.items():
        id = party_id(head)
        p.add("party", id=id, kind="party", polity_id=FRANCE)
        p.add("external_id", entity_id=id, scheme="fr.polynesie.party", value=plain(head))
        p.name(id, label, "fr", "official")
    return p


SYSTEMS: list[tuple[date, date | None, dict[str, object], str]] = [
    (
        date(2013, 1, 1),
        None,
        {
            "formula": "dhondt",
            "threshold": {"level": "list", "scope": "contest", "percent": 5},
            "bonus": {
                "level": "list",
                "size": {"kind": "fraction", "fraction": 0.3333333333, "rounding": "up"},
                "share": {"percent": 50, "strict": True, "rounds": [1]},
            },
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        SECTIONS_NOTE,
    )
]


def assembly(year: int) -> Package:
    p = Package(
        f"fr.polynesie/ap-{year}",
        SOURCE,
        f"The {year} Assembly of French Polynesia election by polling station from the Haut-commissariat",
        depends=["fr.polynesie/registry"],
    )
    p.external |= {FRANCE, key("body", "assembly")}
    p.external.add(territory())
    p.external |= set(COMMUNES.values())
    commune = communes()
    system = key("electoral_system", 2013)
    body = key("body", "assembly")
    rounds = ROUNDS[year]
    data: dict[int, tuple[list[str], list[dict[str, object]]]] = {}
    for round_ in rounds:
        data[round_] = table_2013() if year == 2013 else workbook(f"t{round_}_{year}")
        p.uses(raw.cached(SOURCE, url(f"t{round_}_{year}" if year != 2013 else "t2_2013")))
    if year == 2013:
        p.uses(raw.cached(SOURCE, url("t2_2013_sections")))
    if year == 2023:
        for name in ("t1_2023_pdf", "t2_2023_pdf"):
            p.uses(raw.cached(SOURCE, url(name)))
    units: dict[tuple[str, int], str] = {}
    sections: dict[str, str] = {}
    previous: str | None = None
    first_election: str | None = None
    for round_ in rounds:
        labels, rows = data[round_]
        day = DAYS[year][round_ - 1]
        election = key("election", year, round_)
        p.add(
            "election",
            id=election,
            polity_id=FRANCE,
            parent_id=first_election,
            kind="general" if round_ == 1 else "runoff",
            date_from=day,
            date_to=day,
            status="held",
        )
        first_election = first_election or election
        word = "first" if round_ == 1 else "second"
        p.name(election, f"{year} French Polynesia Assembly election, {word} round", "en", "official")
        p.name(
            election,
            f"Élections territoriales de {year}, {'1er' if round_ == 1 else '2nd'} tour",
            "fr",
            "native",
            False,
        )
        path = f"fr/polynesie/{day}-{'general' if round_ == 1 else 'runoff'}"
        p.add("slug", path=path, entity_id=election, is_current=True)
        contest = key("contest", year, round_)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            district_area_id=territory(),
            magnitude=SEATS,
            round_number=round_,
            previous_round_id=previous,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Assembly of French Polynesia, {word} round", "en", "official")
        p.add("slug", path=f"{path}/assembly", entity_id=contest, is_current=True)
        previous = contest
        result_set = key("result_set", year, round_)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options: list[str] = []
        for position, label in enumerate(labels, start=1):
            option = key("option", year, round_, position)
            options.append(option)
            head = party_of(label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_id(head) if head else None,
                ballot_position=position,
                ballot_label=label,
            )
        declared_seats = SEATS_DECLARED.get((year, round_), {})
        for label, n in declared_seats.items():
            p.add("outcome", result_set_id=result_set, option_id=options[labels.index(label)], kind="won", seats=n)
        for row in rows:
            bureau = int(str(row["bureau"]))
            section = str(row["section"])
            if section not in sections:
                sections[section] = key("section", year, section)
                p.add(
                    "reporting_unit",
                    id=sections[section],
                    polity_id=FRANCE,
                    scheme_code=SECTION_SCHEME,
                    code=f"{year}:{re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', section).encode('ascii', 'ignore').decode().lower())}",
                    area_relation="unlocated",
                    area_id=None,
                    kind="standard",
                )
            name = str(row["commune"])
            if (str(row["section"]) + name, bureau) not in units:
                area = commune.get(plain(name))
                unit = key("unit", year, plain(name), bureau)
                units[(str(row["section"]) + name, bureau)] = unit
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=FRANCE,
                    scheme_code=SCHEME,
                    code=f"{year}:{plain(name)}:{bureau:03d}",
                    parent_id=sections[section],
                    area_relation="within" if area else "unlocated",
                    area_id=area,
                    kind="standard",
                )
            unit = units[(str(row["section"]) + name, bureau)]
            figures = {2: row["registered"], 3: row["voters"], 4: row["valid"]}
            if year != 2013:
                figures |= {6: row["blank"], 5: row["invalid"]}
            for measure, value in figures.items():
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
            votes = row["votes"]
            assert isinstance(votes, list)
            for option, value in zip(options, votes, strict=True):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        totals, listed = declared(year, round_)
        for measure, value in totals.items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in zip(options, listed, strict=False):
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=value)
    return p


def declared(year: int, round_: int) -> tuple[dict[int, int], list[int]]:
    """The authority's own national figures: the published tables for 2023, the territory row of the sheet for 2018."""
    if year == 2023:
        if round_ == 1:
            return DECLARED_2023_1, [14773, 37880, 5423, 43401, 18067, 2458, 2373]
        return DECLARED_2023_2, [56118, 64551, 24989]
    if year == 2018:
        frame = pl.read_excel(
            raw.cached(SOURCE, url(f"t{round_}_2018")).path,
            sheet_name="par section et circo PF",
            engine="calamine",
            has_header=False,
            infer_schema_length=0,
        )
        row = next(r for r in frame.iter_rows() if r[0] and str(r[0]).startswith("CIRCONSCRIPTION"))
        totals = {2: number(row[3]), 3: number(row[4]), 6: number(row[7]), 5: number(row[8]), 4: number(row[9])}
        return totals, [number(row[i]) for i in range(10, len(row), 2) if row[i] is not None]
    if year == 2013:
        return {}, [40441, 62340, 35421]
    return {}, []
