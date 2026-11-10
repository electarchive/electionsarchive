import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.fr.names import person_name
from ea.store import DATA, connect

SOURCE = "fr.regionales"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
STATIC = "https://static.data.gouv.fr"
R15 = f"{STATIC}/resources/elections-regionales-2015-et-des-assemblees-de-corse-de-guyane-et-de-martinique-resultats"
R21 = f"{STATIC}/resources/elections-regionales-2021-resultats-du"
ROUNDS = {
    2004: (date(2004, 3, 21), date(2004, 3, 28)),
    2010: (date(2010, 3, 14), date(2010, 3, 21)),
    2015: (date(2015, 12, 6), date(2015, 12, 13)),
    2021: (date(2021, 6, 20), date(2021, 6, 27)),
}
FILES = {
    (2004, 1, "region"): (
        f"{STATIC}/97/4fb8c291dfaa82ebc74ee253c28ea1c937dbe7d63bda03fd4a16f2a6b99890.xls",
        "Regions T1",
    ),
    (2004, 2, "region"): (
        f"{STATIC}/97/4fb8c291dfaa82ebc74ee253c28ea1c937dbe7d63bda03fd4a16f2a6b99890.xls",
        "Regions T2",
    ),
    (2004, 1, "departement"): (
        f"{STATIC}/97/4fb8c291dfaa82ebc74ee253c28ea1c937dbe7d63bda03fd4a16f2a6b99890.xls",
        "Departements T1",
    ),
    (2004, 2, "departement"): (
        f"{STATIC}/97/4fb8c291dfaa82ebc74ee253c28ea1c937dbe7d63bda03fd4a16f2a6b99890.xls",
        "Departements T2",
    ),
    (2010, 1, "region"): (
        f"{STATIC}/2a/c08b6cecd4d266b84e0b0d57ac3aaf06e06a89d0f034dbc297bb5d0c81df14.xls",
        "Régions T1",
    ),
    (2010, 2, "region"): (
        f"{STATIC}/2a/c08b6cecd4d266b84e0b0d57ac3aaf06e06a89d0f034dbc297bb5d0c81df14.xls",
        "Régions T2",
    ),
    (2010, 1, "departement"): (
        f"{STATIC}/2a/c08b6cecd4d266b84e0b0d57ac3aaf06e06a89d0f034dbc297bb5d0c81df14.xls",
        "Départements T1",
    ),
    (2010, 2, "departement"): (
        f"{STATIC}/2a/c08b6cecd4d266b84e0b0d57ac3aaf06e06a89d0f034dbc297bb5d0c81df14.xls",
        "Départements T2",
    ),
    (2015, 1, "region"): (f"{R15}-tour-1-1/20160121-103706/Reg_15_Resultats_T1_c.xlsx", "Régions"),
    (2015, 2, "region"): (f"{R15}-tour-2-1/20160121-104514/Reg_15_Resultats_T2c.xlsx", "Régions"),
    (2015, 1, "departement"): (f"{R15}-tour-1-1/20160121-103706/Reg_15_Resultats_T1_c.xlsx", "Départements"),
    (2015, 2, "departement"): (f"{R15}-tour-2-1/20160121-104514/Reg_15_Resultats_T2c.xlsx", "Départements"),
    (2021, 1, "region"): (
        f"{R21}-1er-tour-1/20210713-100237/resultats-par-niveau-reg-t1-france-entiere-2021-07-12-18h36.xlsx",
        None,
    ),
    (2021, 2, "region"): (
        f"{R21}-2eme-tour/20210713-095258/reg-resultats-par-niveau-reg-t2-france-entiere-2021-07-12-09h30.xlsx",
        None,
    ),
    (2021, 1, "departement"): (
        f"{R21}-1er-tour-1/20210713-100323/reg-resultats-par-niveau-dpt-t1-france-entiere-2021-07-12-18h37.xlsx",
        None,
    ),
    (2021, 2, "departement"): (
        f"{R21}-2eme-tour/20210713-095456/reg-resultats-par-niveau-dpt-t2-france-entiere-2021-07-12-09h23.xlsx",
        None,
    ),
}
FIELDS = {
    "N°Liste": "panel",
    "Nuance Liste": "nuance",
    "Libellé Abrégé Liste": "short",
    "Libellé Etendu Liste": "long",
    "Nom Tête de Liste": "head",
    "Voix": "votes",
    "Sièges": "seats",
}
OVERSEAS = {"ZA": "971", "ZB": "972", "ZC": "973", "ZD": "974"}
TOTALS = {
    "Inscrits": "registered",
    "Votants": "voters",
    "Blancs et nuls": "invalid",
    "Blancs": "blank",
    "Nuls": "null",
    "Exprimés": "valid",
}


def fetch() -> None:
    for url, _ in FILES.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)


def number(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value).replace(" ", "").replace(",", ".")))


def code_of(value: object) -> str:
    text = str(value).strip()
    text = re.sub(r"\.0$", "", text)
    return text.zfill(2) if text.isdigit() and len(text) < 2 else text


def read(year: int, round_: int, level: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    url, sheet = FILES[year, round_, level]
    frame = pl.read_excel(
        raw.cached(SOURCE, url).path,
        sheet_name=sheet,
        engine="calamine",
        has_header=False,
        infer_schema_length=0,
    )
    rows = [list(row) for row in frame.iter_rows()]
    start = next(i for i, row in enumerate(rows) if "Inscrits" in row)
    header = [str(cell).strip() if cell is not None else "" for cell in rows[start]]
    first = header.index("% Exp/Vot") + 1
    block = header[first : header.index(header[first], first + 1) if header.count(header[first]) > 1 else None]
    block = [cell for cell in block if cell]
    data = [row for row in rows[start + 1 :] if row[first] is not None]
    for names in (
        [FIELDS.get(cell) for cell in block],
        [FIELDS.get(cell) for cell in block if cell != "Composition des Elus"],
    ):
        width = len(names)
        nuance = names.index("nuance")
        if all(
            row[offset + nuance] is None or re.fullmatch(r"L[A-Z]+", str(row[offset + nuance]))
            for row in data
            for offset in range(first, len(row) - width + 1, width)
        ):
            break
    else:
        raise ValueError(f"{year} round {round_} {level}: no consistent list block")
    code = next(i for i, cell in enumerate(header) if cell.startswith("Code d"))
    totals: list[dict[str, object]] = []
    lists: list[dict[str, object]] = []
    for row in rows[start + 1 :]:
        if row[code] is None or str(row[code]).strip() == "":
            continue
        unit = OVERSEAS.get(code_of(row[code]), code_of(row[code]))
        entry: dict[str, object] = {"year": year, "round": round_, "level": level, "unit": unit, "label": row[code + 1]}
        for field, key in TOTALS.items():
            entry[key] = number(row[header.index(field)]) if field in header else None
        totals.append(entry)
        for order, offset in enumerate(range(first, len(row) - width + 1, width), 1):
            block = dict(zip(names, row[offset : offset + width], strict=False))
            if block.get("votes") is None or str(block["votes"]).strip() == "":
                continue
            lists.append(
                {
                    "year": year,
                    "round": round_,
                    "level": level,
                    "unit": unit,
                    "order": order,
                    "panel": number(block.get("panel")),
                    "nuance": str(block["nuance"]).strip() if block.get("nuance") else None,
                    "short": str(block["short"]).strip() if block.get("short") else None,
                    "long": str(block["long"]).strip() if block.get("long") else None,
                    "head": str(block["head"]).strip() if block.get("head") else None,
                    "votes": number(block["votes"]),
                    "seats": number(block.get("seats")),
                }
            )
    return totals, lists


def parse() -> None:
    totals: list[dict[str, object]] = []
    lists: list[dict[str, object]] = []
    for year, round_, level in FILES:
        found_totals, found_lists = read(year, round_, level)
        totals += found_totals
        lists += found_lists
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(PARSED / "totals.parquet")
    pl.DataFrame(lists, infer_schema_length=None).write_parquet(PARSED / "lists.parquet")


OLD_REGIONS = {
    "11": ("Île-de-France", "75 77 78 91 92 93 94 95"),
    "21": ("Champagne-Ardenne", "08 10 51 52"),
    "22": ("Picardie", "02 60 80"),
    "23": ("Haute-Normandie", "27 76"),
    "24": ("Centre", "18 28 36 37 41 45"),
    "25": ("Basse-Normandie", "14 50 61"),
    "26": ("Bourgogne", "21 58 71 89"),
    "31": ("Nord-Pas-de-Calais", "59 62"),
    "41": ("Lorraine", "54 55 57 88"),
    "42": ("Alsace", "67 68"),
    "43": ("Franche-Comté", "25 39 70 90"),
    "52": ("Pays de la Loire", "44 49 53 72 85"),
    "53": ("Bretagne", "22 29 35 56"),
    "54": ("Poitou-Charentes", "16 17 79 86"),
    "72": ("Aquitaine", "24 33 40 47 64"),
    "73": ("Midi-Pyrénées", "09 12 31 32 46 65 81 82"),
    "74": ("Limousin", "19 23 87"),
    "82": ("Rhône-Alpes", "01 07 26 38 42 69 73 74"),
    "83": ("Auvergne", "03 15 43 63"),
    "91": ("Languedoc-Roussillon", "11 30 34 48 66"),
    "93": ("Provence-Alpes-Côte d'Azur", "04 05 06 13 83 84"),
    "94": ("Corse", "2A 2B"),
    "01": ("Guadeloupe", "971"),
    "02": ("Martinique", "972"),
    "03": ("Guyane", "973"),
    "04": ("La Réunion", "974"),
}
NEW_REGIONS = {
    "11": ("Île-de-France", ("11",)),
    "24": ("Centre-Val de Loire", ("24",)),
    "27": ("Bourgogne-Franche-Comté", ("26", "43")),
    "28": ("Normandie", ("23", "25")),
    "32": ("Hauts-de-France", ("31", "22")),
    "44": ("Grand Est", ("21", "41", "42")),
    "52": ("Pays de la Loire", ("52",)),
    "53": ("Bretagne", ("53",)),
    "75": ("Nouvelle-Aquitaine", ("54", "72", "74")),
    "76": ("Occitanie", ("91", "73")),
    "84": ("Auvergne-Rhône-Alpes", ("82", "83")),
    "93": ("Provence-Alpes-Côte d'Azur", ("93",)),
    "94": ("Corse", ("94",)),
    "01": ("Guadeloupe", ("01",)),
    "02": ("Martinique", ("02",)),
    "03": ("Guyane", ("03",)),
    "04": ("La Réunion", ("04",)),
}


def departements(year: int) -> dict[str, str]:
    old = {code: region for region, (_, codes) in OLD_REGIONS.items() for code in codes.split()}
    if year < 2015:
        return old
    merged = {part: region for region, (_, parts) in NEW_REGIONS.items() for part in parts}
    return {code: merged[region] for code, region in old.items()}


REFORM = date(2016, 1, 1)
DATASETS = {
    2004: ("https://www.data.gouv.fr/datasets/536993a5a3a729239d204258", "Licence Ouverte v1.0"),
    2010: ("https://www.data.gouv.fr/datasets/536993a8a3a729239d204260", "Licence Ouverte v1.0"),
    2015: ("https://www.data.gouv.fr/datasets/56a0a693c751df54fbade715", "Licence Ouverte v1.0"),
    2021: ("https://www.data.gouv.fr/datasets/60d1c3d14fc2e0fda6816678", None),
}
ENGLISH = {
    "Bretagne": "Brittany",
    "Normandie": "Normandy",
    "Haute-Normandie": "Upper Normandy",
    "Basse-Normandie": "Lower Normandy",
    "Corse": "Corsica",
    "Bourgogne": "Burgundy",
    "Bourgogne-Franche-Comté": "Burgundy-Franche-Comté",
    "Picardie": "Picardy",
    "Guyane": "French Guiana",
    "La Réunion": "Réunion",
}
COUNCILS = {
    "11": "Conseil régional d'Île-de-France",
    "21": "Conseil régional de Champagne-Ardenne",
    "22": "Conseil régional de Picardie",
    "23": "Conseil régional de Haute-Normandie",
    "24": "Conseil régional du Centre-Val de Loire",
    "25": "Conseil régional de Basse-Normandie",
    "26": "Conseil régional de Bourgogne",
    "27": "Conseil régional de Bourgogne-Franche-Comté",
    "28": "Conseil régional de Normandie",
    "31": "Conseil régional du Nord-Pas-de-Calais",
    "32": "Conseil régional des Hauts-de-France",
    "41": "Conseil régional de Lorraine",
    "42": "Conseil régional d'Alsace",
    "43": "Conseil régional de Franche-Comté",
    "44": "Conseil régional du Grand Est",
    "52": "Conseil régional des Pays de la Loire",
    "53": "Conseil régional de Bretagne",
    "54": "Conseil régional de Poitou-Charentes",
    "72": "Conseil régional d'Aquitaine",
    "73": "Conseil régional de Midi-Pyrénées",
    "74": "Conseil régional du Limousin",
    "75": "Conseil régional de Nouvelle-Aquitaine",
    "76": "Conseil régional d'Occitanie",
    "82": "Conseil régional de Rhône-Alpes",
    "83": "Conseil régional d'Auvergne",
    "84": "Conseil régional d'Auvergne-Rhône-Alpes",
    "91": "Conseil régional de Languedoc-Roussillon",
    "93": "Conseil régional de Provence-Alpes-Côte d'Azur",
    "94": "Assemblée de Corse",
    "01": "Conseil régional de la Guadeloupe",
    "02": "Conseil régional de la Martinique",
    "03": "Conseil régional de la Guyane",
    "04": "Conseil régional de La Réunion",
}
ASSEMBLIES = {"02": "Assemblée de Martinique", "03": "Assemblée de Guyane"}
CORSICA_TERRITORIAL = date(2017, 12, 10)
SECTIONED = {(2015, "03")}
NUANCES = {
    2004: {
        "LXG": "Liste d'extrême gauche",
        "LGA": "Liste de gauche",
        "LDG": "Liste divers gauche",
        "LVE": "Liste des Verts",
        "LEC": "Liste écologiste",
        "LRG": "Liste régionaliste",
        "LCP": "Liste Chasse, pêche, nature et traditions",
        "LDV": "Liste divers",
        "LDR": "Liste de droite",
        "LDD": "Liste divers droite",
        "LFN": "Liste du Front national",
        "LXD": "Liste d'extrême droite",
    },
    2010: {
        "LEXG": "Liste d'extrême gauche",
        "LCOP": "Liste du Front de gauche",
        "LSOC": "Liste socialiste",
        "LUG": "Liste d'union de la gauche",
        "LDVG": "Liste divers gauche",
        "LVEC": "Liste Europe Écologie",
        "LREG": "Liste régionaliste",
        "LAUT": "Autre liste",
        "LCMD": "Liste du Mouvement démocrate",
        "LMAJ": "Liste de la majorité présidentielle",
        "LDVD": "Liste divers droite",
        "LFN": "Liste du Front national",
        "LEXD": "Liste d'extrême droite",
    },
    2015: {
        "LEXG": "Liste d'extrême gauche",
        "LFG": "Liste du Front de gauche",
        "LCOM": "Liste du Parti communiste français",
        "LSOC": "Liste du Parti socialiste",
        "LUG": "Liste d'union de la gauche",
        "LRDG": "Liste du Parti radical de gauche",
        "LDVG": "Liste divers gauche",
        "LVEC": "Liste Europe Écologie-Les Verts",
        "LVEG": "Liste d'union des écologistes et de la gauche",
        "LECO": "Liste écologiste",
        "LREG": "Liste régionaliste",
        "LDIV": "Liste divers",
        "LMDM": "Liste du MoDem",
        "LUDI": "Liste de l'Union des démocrates et indépendants",
        "LUD": "Liste d'union de la droite",
        "LLR": "Liste Les Républicains",
        "LDVD": "Liste divers droite",
        "LDLF": "Liste Debout la France",
        "LFN": "Liste du Front national",
        "LEXD": "Liste d'extrême droite",
    },
    2021: {
        "LEXG": "Liste d'extrême gauche",
        "LCOM": "Liste du Parti communiste français",
        "LFI": "Liste de La France insoumise",
        "LSOC": "Liste du Parti socialiste",
        "LUG": "Liste d'union de la gauche",
        "LUGE": "Liste d'union de la gauche et des écologistes",
        "LDVG": "Liste divers gauche",
        "LECO": "Liste écologiste",
        "LREG": "Liste régionaliste",
        "LDIV": "Liste divers",
        "LREM": "Liste La République en marche",
        "LMDM": "Liste du MoDem",
        "LUDI": "Liste de l'Union des démocrates et indépendants",
        "LUC": "Liste d'union du centre",
        "LUCD": "Liste d'union du centre et de la droite",
        "LDVC": "Liste divers centre",
        "LLR": "Liste Les Républicains",
        "LUD": "Liste d'union de la droite",
        "LDVD": "Liste divers droite",
        "LDSV": "Liste droite souverainiste",
        "LRN": "Liste du Rassemblement national",
        "LEXD": "Liste d'extrême droite",
    },
}
RULES = {
    "formula": "dhondt",
    "tieBreak": "votes",
    "threshold": {"percent": 5, "level": "list", "scope": "contest"},
    "lists": {"order": "closed", "vacancy": "next_on_list"},
}
BONUS = {"level": "list", "share": {"percent": 50, "strict": True, "rounds": [1]}}
SYSTEMS = {
    "regional": (
        "French regional council list system",
        {"kind": "fraction", "fraction": 0.25, "rounding": "up"},
        (
            "Each region elects its council from closed regional lists, divided into sections by département, in up to "
            "two rounds. A list that wins an absolute majority of the valid votes in the first round, or the most votes "
            "in the second, receives a quarter of the seats, rounded up. The remaining seats are shared by the "
            "highest-average (D'Hondt) method among all lists with at least 5 percent of the valid votes, the leading "
            "list included. Each list's seats are then divided among its départemental sections in proportion to the "
            "votes it won in each département. Only lists with at least 10 percent of the first-round valid votes may "
            "stand in the second round, and they may take in candidates from lists that won at least 5 percent."
        ),
    ),
    "corse-2004": (
        "Corsican Assembly list system (2004–2010)",
        {"kind": "seats", "seats": 3},
        (
            "The Assembly of Corsica's 51 members are elected from closed lists in a single island-wide constituency, in "
            "up to two rounds. A list that wins an absolute majority of the valid votes in the first round, or the most "
            "votes in the second, receives 3 bonus seats. The remaining seats are shared by the highest-average "
            "(D'Hondt) method among all lists with at least 5 percent of the valid votes, the leading list included."
        ),
    ),
    "corse-2010": (
        "Corsican Assembly list system (2010–2015)",
        {"kind": "seats", "seats": 9},
        (
            "The Assembly of Corsica's 51 members are elected from closed lists in a single island-wide constituency, "
            "in up to two rounds. A list that wins an absolute majority of the valid votes in the first round, or the "
            "most votes in the second, receives 9 bonus seats. The remaining seats are shared by the highest-average "
            "(D'Hondt) method among all lists with at least 5 percent of the valid votes, the leading list included. "
            "Only lists with at least 7 percent of the first-round valid votes may stand in the second round, and they "
            "may take in candidates from lists that won at least 5 percent."
        ),
    ),
    "corse-2018": (
        "Corsican Assembly list system (from 2017)",
        {"kind": "seats", "seats": 11},
        (
            "Since the Collectivity of Corsica replaced the territorial collectivity and the two départements on 1 January "
            "2018, the Assembly of Corsica has 63 members, elected from closed lists in a single island-wide "
            "constituency, in up to two rounds. A list that wins an absolute majority of the valid votes in the first "
            "round, or the most votes in the second, receives 11 bonus seats. The remaining seats are shared by the "
            "highest-average (D'Hondt) method among all lists with at least 5 percent of the valid votes, the leading "
            "list included. Only lists with at least 7 percent of the first-round valid votes may stand in the second "
            "round, and they may take in candidates from lists that won at least 5 percent."
        ),
    ),
    "ctu": (
        "Martinique and French Guiana assembly list system",
        {"kind": "seats", "seats": 11},
        (
            "Since 2015 the single assemblies of Martinique and French Guiana, 51 members each, are elected from closed "
            "lists divided into sections (four in Martinique, eight in French Guiana), in up to two rounds. A list that "
            "wins an absolute majority of the valid votes in the first round, or the most votes in the second, "
            "receives 11 bonus seats. Lists with at least 5 percent of the valid votes share the other 40 seats. Both "
            "the bonus and the other seats are divided among the sections, so a list's total can differ from an "
            "allocation made across the whole territory."
        ),
    ),
}
METHOD = (
    "Results by region from the Interior Ministry's results file. The ministry's figures by département, loaded "
    "as a second result set, add up exactly to these totals."
)
ROUND_ONE_NOTE = (
    "No list won an absolute majority, so no seats were allocated in this round. Lists that stood again in the "
    "second round under the same head of list are marked as advancing; lists with at least 10 percent of the valid "
    "votes that did not are marked as withdrawn, whether or not they merged into another list."
)


def system_of(code: str, year: int) -> str:
    if code == "94":
        return "corse-2004" if year == 2004 else "corse-2010" if year < 2021 else "corse-2018"
    if code in ASSEMBLIES and year >= 2015:
        return "ctu"
    return "regional"


def allocate(votes: dict[str, int], valid: int, seats: int, size: Mapping[str, object], bonus: bool) -> dict[str, int]:
    leader = max(votes, key=lambda label: votes[label])
    if size["kind"] == "fraction":
        extra = -(-seats // 4) if bonus else 0
    else:
        extra = int(str(size["seats"])) if bonus else 0
    eligible = {label: count for label, count in votes.items() if count * 20 >= valid}
    won = dict.fromkeys(eligible, 0)
    for _ in range(seats - extra):
        best = max(eligible, key=lambda label: (eligible[label] / (won[label] + 1), eligible[label]))
        won[best] += 1
    if extra:
        won[leader] = won.get(leader, 0) + extra
    return {label: count for label, count in won.items() if count}


def head_name(head: str) -> tuple[str | None, str]:
    words = head.split()
    split = next((i for i, word in enumerate(words) if any(c.islower() for c in word)), None)
    if split is None:
        split = len(words) - 1 if len(words) > 1 else len(words)
    return " ".join(words[split:]) or None, " ".join(words[:split])


def emit() -> Path:
    totals = pl.read_parquet(PARSED / "totals.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "French regional elections of 2004, 2010, 2015, and 2021, both rounds, by region and by département, "
                "with each list's seats, from the Interior Ministry's results files"
            ),
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "fr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        france = cs.polity("FR")
        nation = cs.area("fr.nation", "FR")
        sources: dict[int, UUID] = {}
        for year, (url, license) in DATASETS.items():
            title = f"Élections régionales {year}"
            sources[year] = cs.source("Ministère de l'Intérieur", title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher="Ministère de l'Intérieur",
                title=title,
                url=url,
                license=license,
                license_url=None if license is None else "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
            )
            for (file_year, _, _), (file_url, _) in FILES.items():
                if file_year == year:
                    cs.artifact(raw.cached(SOURCE, file_url), sources[year])

        cs.add("scheme", code="fr.region", polity_id=france, kind="administrative", rank=1, noun="region")
        old = departements(2004)
        members: dict[str, list[str]] = {}
        spans: dict[str, tuple[date | None, date | None]] = {}
        labels: dict[str, str] = {}
        for code, (label, codes) in OLD_REGIONS.items():
            members[code] = codes.split()
            labels[code] = label
            spans[code] = (None, None if code in NEW_REGIONS and NEW_REGIONS[code][1] == (code,) else REFORM)
        for code, (label, parts) in NEW_REGIONS.items():
            if parts == (code,):
                labels[code] = label
                continue
            members[code] = [d for d, region in old.items() if region in parts]
            labels[code] = label
            spans[code] = (REFORM, None)
        department_areas = {
            code: cs.area("fr.departement", code, date(2024, 1, 1)) for codes in members.values() for code in codes
        }
        dept_source = conn.execute(
            "select g.source_id from area_geometry g where g.area_id = %s", [department_areas["01"]]
        ).fetchone()
        areas: dict[str, UUID] = {}
        polities: dict[str, UUID] = {}
        for code in members:
            valid_from, valid_to = spans[code]
            label = labels[code]
            areas[code] = cs.area("fr.region", code)
            cs.add("area", id=areas[code], scheme_code="fr.region")
            cs.add("area_code", area_id=areas[code], code=code, valid_from=valid_from, valid_to=valid_to)
            cs.add("area_parent", area_id=areas[code], parent_id=nation, valid_from=valid_from, valid_to=valid_to)
            name(areas[code], label)
            if label in ENGLISH:
                name(areas[code], ENGLISH[label], "en", "english")
            if code == "24":
                name(areas[code], "Centre", "fr", "historical")
            geom, point = conn.execute(
                """with u as (
                     select st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3)) as geom
                     from area_geometry where area_id = any(%s)
                   )
                   select encode(st_asewkb(geom), 'hex'),
                          encode(st_asewkb((select st_pointonsurface(d.geom) from st_dump(geom) d
                                            order by st_area(d.geom::geography) desc limit 1)), 'hex')
                   from u""",
                [[department_areas[d] for d in members[code]]],
            ).fetchall()[0]
            cs.add(
                "area_geometry",
                area_id=areas[code],
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=None if dept_source is None else dept_source[0],
            )
            unit = cs.unit("fr.region", code)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=france,
                scheme_code="fr.region",
                code=code,
                area_relation="equals",
                area_id=areas[code],
                kind="standard",
                valid_from=valid_from,
                valid_to=valid_to,
            )
            name(unit, label)
            polities[code] = cs.key(f"{SOURCE}.polity", code)
            cs.add("polity", id=polities[code], kind="region", iso_code=None)
            name(polities[code], label)
            if label in ENGLISH:
                name(polities[code], ENGLISH[label], "en", "english")
            cs.add(
                "polity_parent", polity_id=polities[code], parent_id=france, valid_from=valid_from, valid_to=valid_to
            )
            cs.add(
                "polity_territory",
                polity_id=polities[code],
                area_id=areas[code],
                valid_from=valid_from,
                valid_to=valid_to,
            )
            cs.add("slug", path=f"fr/region-{slug(label)}", entity_id=polities[code])
        for code, (_, parts) in NEW_REGIONS.items():
            if parts == (code,):
                continue
            for part in parts:
                cs.add(
                    "polity_relation",
                    id=uuid5(NAMESPACE, f"polity_relation\x1f{polities[part]}\x1fmerged_into\x1f{polities[code]}"),
                    from_polity_id=polities[part],
                    to_polity_id=polities[code],
                    kind="merged_into",
                    date=REFORM,
                )

        systems: dict[str, UUID] = {}
        for key, (english, size, note) in SYSTEMS.items():
            systems[key] = cs.key(f"{SOURCE}.electoral_system", key)
            cs.add(
                "electoral_system",
                id=systems[key],
                polity_id=france,
                rules={**RULES, "bonus": {**BONUS, "size": size}},
                rules_version=1,
            )
            name(systems[key], english, "en")
            cs.add("note", entity_id=systems[key], kind="methodology", body_md=note)

        nuances: dict[tuple[int, str], UUID] = {}
        for year, codes in NUANCES.items():
            for code, label in codes.items():
                nuances[year, code] = cs.key(f"{SOURCE}.nuance", f"{year}:{code}")
                cs.add("party", id=nuances[year, code], kind="nuance", polity_id=france)
                name(nuances[year, code], label)
                name(nuances[year, code], code, "fr", "abbreviation")

        persons: dict[str, UUID] = {}

        def person_for(head: str) -> UUID:
            forename, surname = head_name(head)
            key = slug(f"{surname} {forename or ''}")
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key])
                    name(persons[key], person_name(forename, surname))
            return persons[key]

        bodies: dict[str, UUID] = {}
        sizes: dict[str, list[tuple[int, date]]] = {}
        values: list[dict[str, object]] = []
        mismatched: list[str] = []
        for year, days in ROUNDS.items():
            regions = OLD_REGIONS if year < 2015 else NEW_REGIONS
            umbrella = cs.key(f"{SOURCE}.election", str(year))
            cs.add(
                "election",
                id=umbrella,
                polity_id=france,
                kind="local",
                date_from=days[0],
                date_to=days[1],
                status="held",
            )
            name(umbrella, f"{year} French regional elections", "en")
            name(umbrella, f"Élections régionales de {year}", "fr", "native")
            cs.add("slug", path=f"fr/{days[0]}-regional", entity_id=umbrella)
            by_dept = departements(year)
            for code in regions:
                label = labels[code]
                polity = polities[code]
                body_key = f"{code}:assemblee" if code in ASSEMBLIES and year >= 2015 else code
                if body_key not in bodies:
                    bodies[body_key] = cs.key(f"{SOURCE}.body", body_key)
                    cs.add("body", id=bodies[body_key], polity_id=polity, kind="unicameral")
                    council = ASSEMBLIES[code] if body_key.endswith(":assemblee") else COUNCILS[code]
                    name(bodies[body_key], council, "fr", "official")
                    english = (
                        f"Assembly of {ENGLISH.get(label, label)}"
                        if council.startswith("Assemblée")
                        else f"Regional Council of {ENGLISH.get(label, label)}"
                    )
                    name(bodies[body_key], english, "en", "english")
                body = bodies[body_key]
                system_key = system_of(code, year)
                size_rule = SYSTEMS[system_key][1]
                path = f"fr/region-{slug(label)}"
                rows = {
                    round_: lists.filter(
                        (pl.col("year") == year)
                        & (pl.col("round") == round_)
                        & (pl.col("level") == "region")
                        & (pl.col("unit") == code)
                    ).sort("order")
                    for round_ in (1, 2)
                }
                held = [round_ for round_ in (1, 2) if rows[round_].height]
                decisive = held[-1]
                seats = int(rows[decisive]["seats"].sum())
                sizes.setdefault(body_key, []).append((seats, days[decisive - 1]))
                elections: dict[int, UUID] = {}
                previous: UUID | None = None
                round_one_heads: set[str] = set()
                for round_ in held:
                    day = days[round_ - 1]
                    elections[round_] = cs.key(f"{SOURCE}.election", f"{year}:{code}:{round_}")
                    cs.add(
                        "election",
                        id=elections[round_],
                        polity_id=polity,
                        parent_id=umbrella if round_ == 1 else elections[1],
                        kind="general" if round_ == 1 else "runoff",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    suffix = ", second round" if round_ == 2 else ""
                    name(elections[round_], f"{year} {ENGLISH.get(label, label)} regional election{suffix}", "en")
                    kind_path = "general" if round_ == 1 else "runoff"
                    cs.add("slug", path=f"{path}/{day}-{kind_path}", entity_id=elections[round_])
                    contest_key = f"{year}:{code}:{round_}"
                    contest = cs.key(f"{SOURCE}.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=elections[round_],
                        kind="list_allocation",
                        body_id=body,
                        district_area_id=areas[code],
                        magnitude=seats,
                        round_number=round_,
                        previous_round_id=previous,
                        electoral_system_id=systems[system_key],
                        default_result_kind="counted",
                    )
                    round_word = "first" if round_ == 1 else "second"
                    name(contest, f"{year} {ENGLISH.get(label, label)} regional election, {round_word} round", "en")
                    cs.add("slug", path=f"{path}/{day}-{kind_path}/{slug(body_key.split(':')[-1])}", entity_id=contest)
                    previous = contest
                    entries = rows[round_]
                    total = totals.filter(
                        (pl.col("year") == year)
                        & (pl.col("round") == round_)
                        & (pl.col("level") == "region")
                        & (pl.col("unit") == code)
                    ).row(0, named=True)
                    declared = {
                        str(entry["order"]): int(entry["seats"] or 0) for entry in entries.iter_rows(named=True)
                    }
                    votes = {str(entry["order"]): int(entry["votes"]) for entry in entries.iter_rows(named=True)}
                    if round_ == decisive:
                        majority = round_ == 2 or max(votes.values()) * 2 > int(total["valid"])
                        computed = allocate(votes, int(total["valid"]), seats, size_rule, majority)
                        if (
                            computed != {label: n for label, n in declared.items() if n}
                            and (year, code) not in SECTIONED
                        ):
                            mismatched.append(f"{year} {code} {label}: declared {declared}, computed {computed}")
                    options: dict[tuple[str | None, str | None], UUID] = {}
                    outcomes: list[tuple[UUID, str, int | None]] = []
                    next_heads = set(rows[2]["head"].drop_nulls()) if round_ == 1 and 2 in held else set()
                    for entry in entries.iter_rows(named=True):
                        order = str(entry["order"])
                        option = cs.key(f"{SOURCE}.option", f"{contest_key}:{order}")
                        options[entry["nuance"], entry["short"]] = option
                        nuance = nuances.get((year, str(entry["nuance"])))
                        if nuance is None:
                            raise ValueError(f"{year}: no label for nuance {entry['nuance']}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="list",
                            party_id=nuance,
                            ballot_position=entry["panel"],
                            ballot_label=entry["long"] or entry["short"],
                        )
                        if entry["long"] and entry["short"] and entry["long"] != entry["short"]:
                            name(option, entry["short"], "fr", "short")
                        head = str(entry["head"])
                        cs.add(
                            "candidacy",
                            id=cs.key(f"{SOURCE}.candidacy", f"{contest_key}:{order}"),
                            person_id=person_for(head),
                            option_id=option,
                            role="head",
                            list_position=1,
                            ballot_name=head,
                            member_of_party_id=None,
                            is_independent=False,
                        )
                        if round_ == decisive:
                            won = declared[order]
                            outcomes.append((option, "won" if won else "lost", won or None))
                        elif head in next_heads:
                            outcomes.append((option, "advanced", None))
                        elif int(entry["votes"]) * 10 >= int(total["valid"]):
                            outcomes.append((option, "withdrew", None))
                        else:
                            outcomes.append((option, "eliminated", None))
                        if round_ == 1:
                            round_one_heads.add(head)
                    for level, default in (("region", True), ("departement", False)):
                        result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:{level}")
                        cs.add(
                            "result_set",
                            id=result_set,
                            election_id=elections[round_],
                            contest_id=contest,
                            stage="official",
                            kind="counted",
                            status="active",
                            source_id=sources[year],
                            is_default=default,
                            completeness=1,
                        )
                        if default:
                            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
                            if round_ != decisive:
                                cs.add("note", entity_id=result_set, kind="context", body_md=ROUND_ONE_NOTE)
                        for option, kind, won in outcomes:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=won)
                        if level == "region":
                            units = [(cs.unit("fr.region", code), total, entries)]
                        else:
                            units = []
                            for row in totals.filter(
                                (pl.col("year") == year)
                                & (pl.col("round") == round_)
                                & (pl.col("level") == "departement")
                            ).iter_rows(named=True):
                                if by_dept.get(str(row["unit"])) != code:
                                    continue
                                units.append(
                                    (
                                        cs.unit("fr.departement", str(row["unit"]), days[round_ - 1]),
                                        row,
                                        lists.filter(
                                            (pl.col("year") == year)
                                            & (pl.col("round") == round_)
                                            & (pl.col("level") == "departement")
                                            & (pl.col("unit") == row["unit"])
                                        ),
                                    )
                                )
                        for unit, row, unit_lists in units:
                            for field, measure in (
                                ("registered", 2),
                                ("voters", 14),
                                ("valid", 4),
                                ("invalid", 5),
                                ("null", 5),
                                ("blank", 6),
                            ):
                                if row[field] is not None:
                                    values.append(
                                        {
                                            "result_set_id": str(result_set),
                                            "unit_id": str(unit),
                                            "option_id": None,
                                            "measure": measure,
                                            "value": row[field],
                                        }
                                    )
                            for entry in unit_lists.iter_rows(named=True):
                                values.append(
                                    {
                                        "result_set_id": str(result_set),
                                        "unit_id": str(unit),
                                        "option_id": str(options[entry["nuance"], entry["short"]]),
                                        "measure": 1,
                                        "value": entry["votes"],
                                    }
                                )
        if mismatched:
            raise ValueError("seat allocation does not match:\n" + "\n".join(mismatched))
        for body_key, history in sizes.items():
            for index, (seats, start) in enumerate(history):
                if body_key == "94" and start.year == 2015:
                    end: date | None = CORSICA_TERRITORIAL
                elif index + 1 < len(history):
                    end = history[index + 1][1]
                elif body_key in OLD_REGIONS and body_key not in NEW_REGIONS or body_key in ASSEMBLIES:
                    end = REFORM
                else:
                    end = None
                cs.add("body_size", body_id=bodies[body_key], seats=seats, valid_from=start, valid_to=end)
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema_overrides={"value": pl.Int64}, infer_schema_length=None).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
