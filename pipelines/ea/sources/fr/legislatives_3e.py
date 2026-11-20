"""French Chamber of Deputies elections of the Third Republic, 1876 to 1936, by constituency.

The 1914 general election comes first. Its source is the printed results volume `Élections législatives des 26
avril et 10 mai 1914, résultats officiels` (Georges Lachapelle, Paris, 1914), scanned by the University of Toronto
Libraries on the Internet Archive; the pipeline reads its OCR text (`legislatives_3e_ocr`). The election is two
packages beside the shared ones: `fr.legislatives_3e/geo` (one area per constituency), `fr.legislatives_3e/registry`
(the Chamber of Deputies, the electoral system, and the party labels the volume prints), and
`fr.legislatives_3e/1914` (both rounds). A constituency is loaded only when its candidates' printed votes add up to
the printed valid votes; the others are listed in the report as left out.
"""

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import cache
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.fr import legislatives_3e_ocr as ocr
from ea.store import DATA

SOURCE = "fr.legislatives_3e"
REFERENCE = DATA / "reference" / "fr"
GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
SCHEME = "fr.circonscription_3e"
VOLUMES = {
    1914: (
        "https://archive.org/download/lectionslgis01lachuoft/lectionslgis01lachuoft_djvu.txt",
        "https://archive.org/details/lectionslgis01lachuoft",
    )
}
DAYS = {1914: (date(1914, 4, 26), date(1914, 5, 10))}
# The 1914 volume prints the metropolitan constituencies between these lines of its text.
SPAN = {1914: (1437, 8860)}
ELECTED_TOTAL = {1914: 586}
ENGLISH = "Chamber of Deputies"

SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1flachapelle-1914"))
BODY = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fbody\x1fchambre-des-deputes"))
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fscrutin-d-arrondissement-1889"))

# Printed names of the départements, as the OCR reads them, with the current code of the same département where
# one exists. The Seine and Seine-et-Oise were divided in 1964, and Corsica was one département.
DEPARTMENTS: dict[str, tuple[str, str | None]] = {
    "AIN": ("Ain", "01"),
    "AISNE": ("Aisne", "02"),
    "ALLIER": ("Allier", "03"),
    "BASSESALPES": ("Basses-Alpes", "04"),
    "HALTESALPES": ("Hautes-Alpes", "05"),
    "HAUTESALPES": ("Hautes-Alpes", "05"),
    "ALPESMARITIMES": ("Alpes-Maritimes", "06"),
    "ARDECHE": ("Ardèche", "07"),
    "ARDENNES": ("Ardennes", "08"),
    "ARIEGE": ("Ariège", "09"),
    "AUBE": ("Aube", "10"),
    "AUDE": ("Aude", "11"),
    "AVEYRON": ("Aveyron", "12"),
    "BOUCHESDURHONE": ("Bouches-du-Rhône", "13"),
    "CALVADOS": ("Calvados", "14"),
    "CANTAL": ("Cantal", "15"),
    "CHARENTE": ("Charente", "16"),
    "CHARENTEINFERIEURE": ("Charente-Inférieure", "17"),
    "CHER": ("Cher", "18"),
    "CORREZE": ("Corrèze", "19"),
    "CORSE": ("Corse", None),
    "COTEDOR": ("Côte-d'Or", "21"),
    "COTESDUNORD": ("Côtes-du-Nord", "22"),
    "CREUSE": ("Creuse", "23"),
    "DORDOGNE": ("Dordogne", "24"),
    "DOUBS": ("Doubs", "25"),
    "DROME": ("Drôme", "26"),
    "EURE": ("Eure", "27"),
    "EUREETLOIR": ("Eure-et-Loir", "28"),
    "FINISTERE": ("Finistère", "29"),
    "GARD": ("Gard", "30"),
    "HAUTEGARONNE": ("Haute-Garonne", "31"),
    "GERS": ("Gers", "32"),
    "GIRONDE": ("Gironde", "33"),
    "HERAULT": ("Hérault", "34"),
    "ILLEETVILAINE": ("Ille-et-Vilaine", "35"),
    "INDRE": ("Indre", "36"),
    "INDREETLOIRE": ("Indre-et-Loire", "37"),
    "ISERE": ("Isère", "38"),
    "JURA": ("Jura", "39"),
    "LANDES": ("Landes", "40"),
    "LOIRETCHER": ("Loir-et-Cher", "41"),
    "LOIRHETCHER": ("Loir-et-Cher", "41"),
    "LOIRE": ("Loire", "42"),
    "LOIREHAUTE": ("Haute-Loire", "43"),
    "LOIREINFERIEURE": ("Loire-Inférieure", "44"),
    "LOIRET": ("Loiret", "45"),
    "LOT": ("Lot", "46"),
    "LOTETGARONNE": ("Lot-et-Garonne", "47"),
    "LOZERE": ("Lozère", "48"),
    "MAINEETLOIRE": ("Maine-et-Loire", "49"),
    "MANCHE": ("Manche", "50"),
    "MARNE": ("Marne", "51"),
    "MARNEHAUTE": ("Haute-Marne", "52"),
    "MAYENNE": ("Mayenne", "53"),
    "MEURTHEETMOSELLE": ("Meurthe-et-Moselle", "54"),
    "MEUSE": ("Meuse", "55"),
    "MORBIHAN": ("Morbihan", "56"),
    "NIEVRE": ("Nièvre", "58"),
    "NORD": ("Nord", "59"),
    "OISE": ("Oise", "60"),
    "ORNE": ("Orne", "61"),
    "PASDECALAIS": ("Pas-de-Calais", "62"),
    "PUYDEDOME": ("Puy-de-Dôme", "63"),
    "PYRENEESBASSES": ("Basses-Pyrénées", "64"),
    "PYRENEESHAUTES": ("Hautes-Pyrénées", "65"),
    "PYRENEESORIENTALES": ("Pyrénées-Orientales", "66"),
    "RHINHAUT": ("Territoire de Belfort", "90"),
    "RHONE": ("Rhône", "69"),
    "SAONEHAUTE": ("Haute-Saône", "70"),
    "SAONEETLOIRE": ("Saône-et-Loire", "71"),
    "SARTHE": ("Sarthe", "72"),
    "SAVOIE": ("Savoie", "73"),
    "SAVOIEHAUTE": ("Haute-Savoie", "74"),
    "SEINE": ("Seine", None),
    "SEINEINFERIEURE": ("Seine-Inférieure", "76"),
    "SEINEETMARNE": ("Seine-et-Marne", "77"),
    "SEINEETOISE": ("Seine-et-Oise", None),
    "SEVRESDEUX": ("Deux-Sèvres", "79"),
    "SOMME": ("Somme", "80"),
    "TARN": ("Tarn", "81"),
    "TARNETGARONNE": ("Tarn-et-Garonne", "82"),
    "VAR": ("Var", "83"),
    "VAUCLUSE": ("Vaucluse", "84"),
    "VENDEE": ("Vendée", "85"),
    "VIENNE": ("Vienne", "86"),
    "VIENNEHAUTE": ("Haute-Vienne", "87"),
    "VOSGES": ("Vosges", "88"),
    "YONNE": ("Yonne", "89"),
}
# The party labels the volume prints, read through the letters alone because the OCR garbles the punctuation.
LABELS: dict[str, tuple[str, str]] = {
    "ps": ("P. S.", "Parti socialiste (S.F.I.O.)"),
    "prd": ("P. R. D.", "Parti républicain démocratique"),
    "radu": ("Rad. U.", "Radicaux unifiés"),
    "ru": ("Rad. U.", "Radicaux unifiés"),
    "rad": ("Rad.", "Radicaux"),
    "radind": ("Rad. Ind.", "Radicaux indépendants"),
    "replib": ("Rép. Lib.", "Républicains libéraux"),
    "replih": ("Rép. Lib.", "Républicains libéraux"),
    "repdem": ("Rép. Dém.", "Républicains démocrates"),
    "repdeni": ("Rép. Dém.", "Républicains démocrates"),
    "repsoc": ("Rép. Soc.", "Républicains socialistes"),
    "fr": ("F. R.", "Fédération républicaine"),
    "progr": ("Progr.", "Progressistes"),
    "droite": ("Droite", "Droite"),
    "ind": ("Ind.", "Indépendants"),
    "rep": ("Rép.", "Républicains"),
    "repind": ("Rép. Ind.", "Républicains indépendants"),
    "lib": ("Lib.", "Libéraux"),
    "lih": ("Lib.", "Libéraux"),
    "socind": ("Soc. Ind.", "Socialistes indépendants"),
    "fdg": ("F. D. G.", "F. D. G."),
    "cons": ("Cons.", "Conservateurs"),
}
METHOD = (
    "Votes, registered voters, and spoiled ballots are the figures printed for each constituency in the volume's "
    "results tables, read from its OCR text. A constituency is kept only when its candidates' first-round votes add "
    "up to the printed valid votes (suffrages exprimés), after correcting at most one single-digit misreading that "
    "alone closes the gap. Valid votes, registered voters, and spoiled ballots are printed for the first "
    "round only. The party labels are the volume's own classification of each candidate, not a ballot label."
)
SYSTEM_NOTE = (
    "Single-member constituencies, one for each arrondissement or part of one, restored in 1889. A candidate was "
    "elected in the first round with more than half the valid votes and a quarter of the registered voters; "
    "otherwise a second round was held two weeks later, open to any candidate, and a plurality elected. The rules "
    "list `advance` as 99 because every candidate could stand in the second round."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def letters(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.upper().replace("Œ", "OE"))
    return re.sub(r"[^A-Z]", "", "".join(c for c in folded if not unicodedata.combining(c)))


def fold(text: str) -> str:
    return letters(text).lower()


@dataclass
class Held:
    france: str
    nation: str
    departments: dict[str, str]
    towns: dict[str, list[str]]


@cache
def reference() -> Held:
    polities = pl.read_parquet(REFERENCE / "polity.parquet").filter(pl.col("iso_code") == "FR")
    if polities.height != 1:
        raise ValueError("France is not in the reference partition")
    france = str(polities["id"][0])
    areas = pl.read_parquet(REFERENCE / "area.parquet").join(
        pl.read_parquet(REFERENCE / "area_code.parquet"), left_on="id", right_on="area_id"
    )
    nation = areas.filter(pl.col("scheme_code") == "fr.nation")["id"][0]
    departments = areas.filter(pl.col("scheme_code") == "fr.departement")
    towns: dict[str, list[str]] = {}
    for row in areas.filter(pl.col("scheme_code") == "fr.commune").iter_rows(named=True):
        towns.setdefault(str(row["code"])[:2], []).append(str(row["name"]))
    return Held(france, nation, dict(zip(departments["code"], departments["id"], strict=True)), towns)


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/{year}" for year in sorted(VOLUMES))]


def fetch() -> None:
    for url, _ in VOLUMES.values():
        raw.fetch(SOURCE, url, suffix=".txt")


def lines(year: int) -> list[str]:
    artifact = raw.cached(SOURCE, VOLUMES[year][0])
    return artifact.path.read_text(encoding="utf8").split("\n")


@dataclass
class Seat:
    block: ocr.Block
    department: str
    code: str | None
    index: int
    name: str
    number: int
    area: str
    unit: str


def clean_name(text: str) -> str:
    text = re.sub(r"[^\w\s'’.\-]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\d", "", text)
    return re.sub(r"\s+", " ", text).strip(" .-'")


def place_name(held: Held, code: str | None, text: str) -> str:
    text = clean_name(re.sub(r"\b(?:arr(?:ondissement|\.)?)\b", "", text, flags=re.IGNORECASE))
    pool = held.towns.get(code or "", [])
    folded = {fold(town): town for town in pool}
    near = difflib.get_close_matches(fold(text), list(folded), n=1, cutoff=0.78)
    return folded[near[0]] if near else text


def seats(year: int, held: Held) -> tuple[list[Seat], list[ocr.Block]]:
    start, stop = SPAN[year]
    found = ocr.blocks(lines(year), start, stop)
    for block in found:
        ocr.settle(block)
    kept: list[Seat] = []
    left: list[ocr.Block] = []
    counts: dict[str, int] = {}
    for block in found:
        label = letters(block.department)
        label = {"HAUTESALPES": "HALTESALPES", "HAUTEQARONNE": "HAUTEGARONNE", "TARNETQARONNE": "TARNETGARONNE"}.get(
            label, label
        )
        if label not in DEPARTMENTS:
            label = next(iter(difflib.get_close_matches(label, list(DEPARTMENTS), n=1, cutoff=0.8)), "")
        if label not in DEPARTMENTS:
            block.problem = f"unknown département {block.department!r}"
        if block.problem is not None:
            left.append(block)
            continue
        name, code = DEPARTMENTS[label]
        counts[label] = counts.get(label, 0) + 1
        index = counts[label]
        slot = f"{year}:{label.lower()}:{block.line}"
        kept.append(
            Seat(
                block,
                name,
                code,
                index,
                place_name(held, code, block.name),
                0,
                key("area", slot),
                key("unit", slot),
            )
        )
    return kept, left


def party_key(label: str) -> str | None:
    read = fold(label)
    return read if read in LABELS else None


def geo(year: int = 1914) -> Package:
    held = reference()
    kept, _ = seats(year, held)
    p = Package(GEO, SOURCE, f"Constituencies of the {year} French Chamber of Deputies election, without outlines")
    p.external.add(held.france)
    p.external.add(held.nation)
    p.add("scheme", code=SCHEME, polity_id=held.france, kind="electoral_district", rank=1, noun="constituency")
    for seat in kept:
        p.add("area", id=seat.area, scheme_code=SCHEME)
        p.add("area_code", area_id=seat.area, code=f"{year}:{seat.block.line}")
        parent = held.departments.get(seat.code or "", held.nation)
        if parent != held.nation:
            p.external.add(parent)
        p.add("area_parent", area_id=seat.area, parent_id=parent)
        p.name(seat.area, title_of(seat), "fr", "official")
    return p


def circ_number(text: str) -> int | None:
    found = re.search(r"[1-9]", text)
    if found:
        return int(found.group())
    return 1 if re.match(r"^\W*[lIi!]", text) else None


def title_of(seat: Seat) -> str:
    number = circ_number(seat.block.circ)
    part = "" if number is None else (" (1re circonscription)" if number == 1 else f" ({number}e circonscription)")
    return clean_title(f"{seat.name}{part}, {seat.department}")


def clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'’.,()\-]", " ", text)).strip()


def registry() -> Package:
    held = reference()
    p = Package(
        REGISTRY,
        SOURCE,
        "The Chamber of Deputies of the Third Republic, its 1889 electoral system, and the party labels of the 1914 "
        "results volume",
        depends=[GEO],
    )
    p.external.add(held.france)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="compendium",
        publisher="Georges Lachapelle, Librairie des publications officielles et des sciences économiques et sociales",
        title="Élections législatives des 26 avril et 10 mai 1914: résultats officiels (Paris, 1914)",
        url=VOLUMES[1914][1],
        license="Public domain: published in 1914",
        license_url="https://creativecommons.org/publicdomain/mark/1.0/",
        attribution="Digitized by the University of Toronto Libraries, Internet Archive",
    )
    for year in VOLUMES:
        p.artifact(raw.cached(SOURCE, VOLUMES[year][0]), SOURCE_ID)
    p.add("body", id=BODY, polity_id=held.france, kind="lower_house")
    p.name(BODY, ENGLISH, "en", "official")
    p.name(BODY, "Chambre des députés", "fr", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=held.france,
        valid_from=date(1889, 7, 17),
        valid_to=date(1919, 1, 1),
        rules={
            "formula": "two_round",
            "majority": 0.5,
            "advance": 99,
            "minimum": {"share": 0.25, "of": "registered"},
        },
        rules_version=1,
    )
    p.name(SYSTEM, "Single-member constituencies in two rounds, 1889", "en", "official")
    p.add("note", id=key("note", "system"), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for short, name in sorted({value for value in LABELS.values()}):
        id = party(short)
        p.add("party", id=id, kind="nuance", polity_id=held.france)
        p.name(id, name, "fr", "official")
        p.name(id, short, "fr", "abbreviation", False)
    return p


def party(short: str) -> str:
    return key("party", short)


def election(year: int = 1914) -> Package:
    held = reference()
    kept, left = seats(year, held)
    days = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} French Chamber of Deputies election, both rounds, by constituency",
        depends=[GEO, REGISTRY],
    )
    p.external.add(held.france)
    p.uses(raw.cached(SOURCE, VOLUMES[year][0]))
    elections: dict[int, str] = {}
    paths: dict[int, str] = {}
    for round_, day in enumerate(days, start=1):
        elections[round_] = key("election", year, round_)
        paths[round_] = f"fr/{day}-{'general' if round_ == 1 else 'runoff'}"
        p.add(
            "election",
            id=elections[round_],
            polity_id=held.france,
            parent_id=None if round_ == 1 else elections[1],
            kind="general" if round_ == 1 else "runoff",
            date_from=day,
            date_to=day,
            status="held",
        )
        suffix = "" if round_ == 1 else ", second round"
        p.name(elections[round_], f"{year} French legislative election{suffix}", "en", "official")
        p.name(
            elections[round_],
            f"Élections législatives de {year}" + ("" if round_ == 1 else ", second tour"),
            "fr",
            "native",
            False,
        )
        p.add("slug", path=paths[round_], entity_id=elections[round_], is_current=True)
    used: set[str] = set()
    persons: dict[str, str] = {}
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []
    totals = {"registered": 0, "valid": 0, "spoiled": 0, "second": 0, "elected": 0}
    for seat in kept:
        block = seat.block
        people = block.candidates
        decisive = block.rounds
        previous: str | None = None
        title = title_of(seat)
        p.add(
            "reporting_unit",
            id=seat.unit,
            polity_id=held.france,
            scheme_code=SCHEME,
            code=f"{year}:{block.line}",
            area_relation="equals",
            area_id=seat.area,
            kind="standard",
        )
        for round_ in range(1, decisive + 1):
            contest_key = f"{year}:{block.line}:{round_}"
            contest = key("contest", contest_key)
            p.add(
                "contest",
                id=contest,
                election_id=elections[round_],
                kind="body_seats",
                body_id=BODY,
                district_area_id=seat.area,
                magnitude=1,
                round_number=round_,
                previous_round_id=previous,
                electoral_system_id=SYSTEM,
                default_result_kind="counted",
            )
            word = "" if decisive == 1 else (", first round" if round_ == 1 else ", second round")
            p.name(contest, f"{title}{word}", "fr", "official")
            p.add(
                "slug",
                path=f"{paths[round_]}/{slug(seat.department)}-{slug(seat.name)}-{block.line}",
                entity_id=contest,
                is_current=True,
            )
            previous = contest
            result_set = key("result_set", contest_key)
            p.add(
                "result_set",
                id=result_set,
                election_id=elections[round_],
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=SOURCE_ID,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD,
                lang="en",
            )
            if block.repair:
                p.add(
                    "note",
                    id=key("note", result_set, "repair"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"One number in the OCR text was misread and is corrected so the candidates' votes add "
                    f"up to the printed valid votes ({block.repair}).",
                    lang="en",
                )
            if round_ == 1:
                for measure, value in ((2, block.registered), (4, block.valid), (5, block.spoiled)):
                    if value is not None:
                        sets.append(result_set)
                        unit_ids.append(seat.unit)
                        option_ids.append(None)
                        measures.append(measure)
                        values.append(value)
            for order, person in enumerate(people, start=1):
                votes = person.first if round_ == 1 else person.second
                if votes is None:
                    continue
                option = key("option", contest_key, order)
                scatter = bool(re.match(r"^Div", person.name))
                short = None if scatter else party_key(person.label)
                if short is not None:
                    used.add(LABELS[short][0])
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="others" if scatter else "candidate",
                    party_id=party(LABELS[short][0]) if short is not None else None,
                    ballot_position=order,
                    ballot_label="Divers" if scatter else clean_name(person.name),
                )
                if not scatter:
                    who = f"{year}:{seat.department}:{block.line}:{order}"
                    if who not in persons:
                        persons[who] = key("person", who)
                        p.add("person", id=persons[who])
                        p.name(persons[who], clean_name(person.name), "fr", "official")
                    p.add(
                        "candidacy",
                        id=key("candidacy", contest_key, order),
                        person_id=persons[who],
                        option_id=option,
                        role="head",
                        ballot_name=clean_name(person.name),
                        member_of_party_id=party(LABELS[short][0]) if short is not None else None,
                        is_independent=False,
                        is_incumbent=person.incumbent,
                    )
                if round_ == decisive:
                    outcome = "elected" if person.elected else "lost"
                else:
                    outcome = "advanced" if person.second is not None else "lost"
                if not scatter:
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind=outcome,
                        seats=1 if outcome == "elected" else None,
                    )
                sets.append(result_set)
                unit_ids.append(seat.unit)
                option_ids.append(option)
                measures.append(1)
                values.append(votes)
        totals["registered"] += block.registered or 0
        totals["valid"] += block.valid or 0
        totals["spoiled"] += block.spoiled or 0
        totals["elected"] += 1
        if decisive == 2:
            totals["second"] += 1
    size = len(values)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": sets,
                "unit_id": unit_ids,
                "option_id": option_ids,
                "channel": [0] * size,
                "measure": measures,
                "count_round": [0] * size,
                "flags": [0] * size,
                "value": values,
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        ),
    )
    report = {
        "year": year,
        "constituencies": len(kept),
        "left_out": [
            {"department": block.department, "name": block.name, "line": block.line, "reason": block.problem}
            for block in left
        ],
        "totals": totals,
        "volume_constituencies": ELECTED_TOTAL[year],
    }
    out = DATA / "parsed" / SOURCE
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{year}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"{year}: {len(kept)} constituencies kept, {len(left)} left out; valid votes {totals['valid']:,}", flush=True)
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for year in sorted(VOLUMES):
        election(year).write()


def emit() -> None:
    package.run("load", packages())
