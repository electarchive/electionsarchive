import json
import re
import time
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.fr import cdsp
from ea.store import DATA, connect

SOURCE = "fr.legislatives_1981_overseas"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://fr.wikipedia.org/w/api.php?"
PUBLISHER = "Wikipédia"
SOURCE_TITLE = "Élections législatives de 1981 outre-mer, par circonscription"
LANDING = "https://fr.wikipedia.org/wiki/%C3%89lections_l%C3%A9gislatives_fran%C3%A7aises_de_1981"
BOOK_TITLE = "Les élections législatives de 1981"
BOOK_URL = "https://bibliotheques-specialisees.paris.fr/ark:/73873/pf0000304897"
COUNCIL_URL = "https://www.conseil-constitutionnel.fr/decision/1981/81959an.htm"
COUNCIL_TITLE = "Décision n° 81-959 AN du 9 octobre 1981"
MAIN = (date(1981, 6, 14), date(1981, 6, 21))
WALLIS = (date(1981, 6, 21), date(1981, 6, 28))
POLYNESIA = (date(1981, 6, 21), date(1981, 7, 5))
PLACES = {
    "971": ("Élections législatives de 1981 en Guadeloupe", 3, MAIN),
    "972": ("Élections législatives de 1981 en Martinique", 3, MAIN),
    "973": ("Élections législatives de 1981 en Guyane", 1, MAIN),
    "974": ("Élections législatives de 1981 à La Réunion", 3, MAIN),
    "975": ("Élections législatives de 1981 à Saint-Pierre-et-Miquelon", 1, MAIN),
    "976": ("Élections législatives de 1981 à Mayotte", 1, MAIN),
    "986": ("Élection législative de 1981 à Wallis-et-Futuna", 1, WALLIS),
    "987": ("Élections législatives de 1981 en Polynésie française", 2, POLYNESIA),
    "988": ("Élections législatives de 1981 en Nouvelle-Calédonie", 2, MAIN),
}
ACCOUNTING = {
    "Inscrits": cdsp.REGISTERED,
    "Votants": cdsp.VOTERS,
    "Exprimés": cdsp.VALID,
    "Blancs et nuls": cdsp.INVALID,
}
TENDENCIES = {
    "PCF": "Parti communiste",
    "PS": "Parti socialiste",
    "MRG": "Mouvement des radicaux de gauche",
    "RPR": "Rassemblement pour la République",
    "UDF": "Union pour la démocratie française",
    "diss. RPR": "Rassemblement pour la République",
    "Divers droite": "Divers droite",
    "Divers gauche": "Divers gauche",
    "Extrême gauche": "Extrême gauche",
    "CO": "Extrême gauche",
    "Divers écologiste": "Écologistes",
    "Régionaliste": "Régionalistes",
    "Extrême droite": "Extrême droite",
}
METHOD = (
    "Candidates, parties, and votes by round from the French Wikipedia's article on the 1981 legislative election in "
    "this territory, which reproduces the Interior Ministry's results; registered voters, voters, and valid votes "
    "from the same table. Wallis-et-Futuna voted on 21 and 28 June and French Polynesia on 21 June and 5 July, as in "
    "the ministry's volume on the election; metropolitan France, the overseas départements, Mayotte, Saint-Pierre-et-"
    "Miquelon, and New Caledonia voted on 14 and 21 June."
)
CAVEAT = (
    "The parties are as the article prints them: the national tendencies are the same entities as in the metropolitan "
    "results, and local parties stand as their own entities."
)


class Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.depth = 0
        self.cell: list[str] | None = None
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = {key: value or "" for key, value in attrs}
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.tables.append([])
        elif self.depth != 1:
            return
        elif tag == "tr":
            self.tables[-1].append([])
        elif tag in ("td", "th") and self.tables[-1]:
            self.cell = []
            self.tables[-1][-1].append("")
        elif self.cell is not None and (
            tag in ("sup", "style") or "display:none" in found.get("style", "").replace(" ", "")
        ):
            self.hidden += 1
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.depth:
            self.depth -= 1
        elif self.depth != 1:
            return
        elif self.hidden and tag in ("sup", "style", "span"):
            self.hidden -= 1
        elif tag in ("td", "th") and self.cell is not None:
            self.tables[-1][-1][-1] = " ".join("".join(self.cell).split())
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None and not self.hidden:
            self.cell.append(data)


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "text", "format": "json", "formatversion": 2, "redirects": 1}
    )


def fetch() -> None:
    for title, _, _ in PLACES.values():
        try:
            raw.cached(SOURCE, page(title))
        except LookupError:
            raw.fetch(SOURCE, page(title), suffix=".json")
            time.sleep(2)
    raw.fetch(SOURCE, COUNCIL_URL, suffix=".html")


def integer(cell: str) -> int | None:
    squeezed = re.sub(r"[\s \xa0]", "", cell)
    return int(squeezed) if re.fullmatch(r"\d+", squeezed) else None


def parse() -> None:
    candidates: list[dict[str, object]] = []
    accounting: list[dict[str, object]] = []
    for code, (title, seats, days) in PLACES.items():
        reader = Tables()
        reader.feed(json.loads(raw.cached(SOURCE, page(title)).path.read_text())["parse"]["text"])
        tables = [table for table in reader.tables if table and any("Candidat" in cell for cell in table[0])]
        if len(tables) != seats:
            raise ValueError(f"{title}: {len(tables)} constituency tables, expected {seats}")
        for circ, table in enumerate(tables, start=1):
            rounds = 2 if any("Second tour" in cell for row in table[:2] for cell in row) else 1
            for row in table:
                cells = [cell for cell in row if cell]
                if not cells or cells[0] in ("Candidat", "Voix") or cells[0].startswith("Source"):
                    continue
                numbers = [value for cell in cells[1:] if (value := integer(cell)) is not None]
                if cells[0] in ACCOUNTING or cells[0] == "Abstentions":
                    if cells[0] in ACCOUNTING:
                        for number, value in enumerate(numbers, start=1):
                            accounting.append(
                                {
                                    "code": code,
                                    "circ": circ,
                                    "round": number,
                                    "measure": ACCOUNTING[cells[0]],
                                    "value": value,
                                }
                            )
                    continue
                name = cells[0]
                elected = re.search(r"(?:^| )(?:ré)?élue?$", name) is not None
                name = re.sub(r"(?: (?:sortante?|(?:ré)?élue?))+$", "", name)
                party = cells[1]
                counts = [value for cell in cells[2:] if (value := integer(cell)) is not None and "," not in cell]
                if not counts or len(counts) > rounds:
                    raise ValueError(f"{title}: unreadable row {cells}")
                for number, value in enumerate(counts, start=1):
                    candidates.append(
                        {
                            "code": code,
                            "circ": circ,
                            "round": number,
                            "name": name,
                            "party": party,
                            "votes": value,
                            "elected": elected and number == len(counts),
                        }
                    )
    frame = pl.DataFrame(candidates)
    totals = pl.DataFrame(accounting)
    valid = totals.filter(pl.col("measure") == cdsp.VALID).select("code", "circ", "round", valid="value")
    summed = frame.group_by("code", "circ", "round").agg(
        pl.col("votes").sum(), pl.col("elected").sum().alias("winners")
    )
    check = summed.join(valid, on=("code", "circ", "round"), how="left")
    off = check.filter(pl.col("votes") != pl.col("valid"))
    if off.height:
        raise ValueError(f"candidate votes differ from valid votes:\n{off}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    totals.write_parquet(PARSED / "totals.parquet")
    print(frame.group_by("party").len().sort("party"))
    print(frame.filter(pl.col("elected")).group_by("code").len().sort("code"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The 1981 French legislative election in the overseas départements and territories, by constituency, from the French Wikipedia",
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, SOURCE_TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher=PUBLISHER,
            title=SOURCE_TITLE,
            url=LANDING,
            license="CC BY-SA 4.0",
            notes=f"Reproduces the Interior Ministry's volume {BOOK_TITLE} ({BOOK_URL}).",
        )
        for title, _, _ in PLACES.values():
            cs.artifact(raw.cached(SOURCE, page(title)), source)
        council = cs.source("Conseil constitutionnel", COUNCIL_TITLE)
        cs.add(
            "source",
            id=council,
            kind="official_body",
            publisher="Conseil constitutionnel",
            title=COUNCIL_TITLE,
            url=COUNCIL_URL,
            license="Freely reproducible with the Conseil constitutionnel's site cited as the source, without altering the text (legal notice)",
            notes="Rules on the Wallis-et-Futuna election held on 28 June 1981.",
        )
        cs.artifact(raw.cached(SOURCE, COUNCIL_URL), council)

        france = cs.polity("FR")
        assembly = cs.key("fr.body", "national-assembly")
        system = cs.key("fr.electoral_system", "legislative-two-round")
        cycle = cs.key("fr.election", f"{MAIN[0]}:legislative")
        if assembly in cs.minted or system in cs.minted or cycle in cs.minted:
            raise ValueError("publish fr.cdsp before fr.legislatives_1981_overseas")
        elections: dict[tuple[date, int], UUID] = {}
        for days in (MAIN, WALLIS, POLYNESIA):
            for number, day in enumerate(days, start=1):
                key = cs.key("fr.election", str(day) if (day, number) != (WALLIS[0], 1) else f"{day}:overseas")
                added = key in elections.values()
                elections[(day, number)] = key
                if key not in cs.minted or added:
                    continue
                stage = "general" if number == 1 else "runoff"
                cs.add(
                    "election",
                    id=key,
                    polity_id=france,
                    parent_id=cycle,
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                where = {
                    (WALLIS[0], 1): " (Wallis-et-Futuna and French Polynesia)",
                    (WALLIS[1], 2): " (Wallis-et-Futuna)",
                    (POLYNESIA[1], 2): " (French Polynesia)",
                }[(day, number)]
                name(key, f"1981 French legislative election, {'first' if number == 1 else 'second'} round{where}")
                cs.add("slug", path=f"fr/{day}-{stage}", entity_id=key)

        parties: dict[str, UUID] = {}

        def party_of(label: str) -> UUID | None:
            if label == "Sans étiquette":
                return None
            if label not in parties:
                base = re.sub(r" \(.*\)$", "", label)
                tendency = TENDENCIES.get(label) or TENDENCIES.get(base)
                if tendency is not None:
                    parties[label] = cs.key(f"{cdsp.SOURCE}.tendency", f"1981:{slug(tendency)}")
                    if parties[label] in cs.minted:
                        raise ValueError(f"tendency {tendency} is missing; publish fr.cdsp first")
                else:
                    parties[label] = cs.key(f"{SOURCE}.party", slug(base))
                    if parties[label] in cs.minted:
                        cs.add("party", id=parties[label], kind="party", polity_id=france)
                        name(parties[label], base, "fr", "abbreviation")
                        name(parties[label], base, "fr")
            return parties[label]

        persons: dict[str, UUID] = {}
        previous: dict[str, UUID] = {}
        added: set[UUID] = set()
        for (code, circ, number), lines in frame.sort("code", "circ", "round").group_by(
            "code", "circ", "round", maintain_order=True
        ):
            title, seats, days = PLACES[code]
            single = seats == 1
            day = days[number - 1]
            stage = "general" if number == 1 else "runoff"
            ordinal_word = "first" if number == 1 else "second"
            election = elections[(day, number)]
            dept_area = cs.area("fr.departement", code)
            if dept_area in cs.minted:
                raise ValueError(f"no département {code}")
            found = conn.execute(
                "select n.text from name n where n.entity_id = %s and n.is_preferred", [dept_area]
            ).fetchone()
            if found is None:
                raise ValueError(f"no name for département {code}")
            district = found[0]
            if single:
                unit = cs.unit("fr.departement", code, on=day)
                area = dept_area
                contest_key = f"{day}:{code}"
                label = f"{district}, 1981 legislative election, {ordinal_word} round"
                path = f"fr/{day}-{stage}/{slug(district)}"
                chain = code
            else:
                circ_code = f"{code}{circ:02d}"
                unit = cs.unit("fr.circonscription", circ_code, on=MAIN[0])
                area = cs.area("fr.circonscription", circ_code, on=MAIN[0])
                if area in cs.minted and area not in added:
                    added.add(area)
                    cs.add("area", id=area, scheme_code="fr.circonscription")
                    cs.add("area_code", area_id=area, code=circ_code, valid_from=MAIN[0], valid_to=cdsp.MAPS[1][1])
                    cs.add(
                        "area_parent", area_id=area, parent_id=dept_area, valid_from=MAIN[0], valid_to=cdsp.MAPS[1][1]
                    )
                    name(area, f"{district}, {circ}{'re' if circ == 1 else 'e'} circonscription", "fr")
                if unit in cs.minted and unit not in added:
                    added.add(unit)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=france,
                        scheme_code="fr.circonscription",
                        code=circ_code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=MAIN[0],
                        valid_to=cdsp.MAPS[1][1],
                    )
                    name(unit, f"{district}, {circ}{'re' if circ == 1 else 'e'} circonscription", "fr")
                contest_key = f"{day}:{circ_code}"
                label = f"{district}, constituency {circ}, {ordinal_word} round"
                path = f"fr/{day}-{stage}/{slug(district)}-{circ}"
                chain = circ_code
            contest = cs.key("fr.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=assembly,
                district_area_id=area,
                magnitude=1,
                round_number=number,
                previous_round_id=previous.get(chain),
                electoral_system_id=system,
                default_result_kind="counted",
            )
            previous[chain] = contest
            name(contest, label)
            cs.add("slug", path=path, entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEAT)
            if result_set in cs.minted:
                for measure, value in (
                    totals.filter((pl.col("code") == code) & (pl.col("circ") == circ) & (pl.col("round") == number))
                    .select("measure", "value")
                    .iter_rows()
                ):
                    cs.add(
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
            later = (
                set(frame.filter((pl.col("code") == code) & (pl.col("circ") == circ) & (pl.col("round") == 2))["name"])
                if number == 1
                else set()
            )
            for line in lines.sort("votes", descending=True).iter_rows(named=True):
                option_key = f"{contest_key}:{slug(line['name'])}"
                option = cs.key("fr.option", option_key)
                party = party_of(line["party"])
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=line["name"],
                )
                person_key = f"{code}:{cdsp.fold(line['name'])}"
                if person_key not in persons:
                    persons[person_key] = cs.key(f"{SOURCE}.person", person_key)
                    if persons[person_key] in cs.minted:
                        cs.add("person", id=persons[person_key])
                        name(persons[person_key], line["name"], "fr")
                cs.add(
                    "candidacy",
                    id=cs.key("fr.candidacy", option_key),
                    person_id=persons[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=line["name"],
                    is_independent=party is None,
                )
                outcome = "elected" if line["elected"] else "advanced" if line["name"] in later else None
                if outcome is not None:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
                if result_set in cs.minted:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=cdsp.VOTES,
                        count_round=0,
                        flags=0,
                        value=line["votes"],
                    )
        return cs.write()
