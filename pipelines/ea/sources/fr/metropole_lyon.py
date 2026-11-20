"""The Métropole de Lyon council elections of 2020 and 2026, by metropolitan constituency and by polling station.

The 150 councillors are elected directly in 14 constituencies on the municipal election day, by two-round list
voting. Results come from the Interior Ministry's open data on data.gouv.fr and, for the 2026 constituency totals,
its results pages; the constituency outlines come from the Métropole de Lyon's open data. The 2020 files name only the
head of each list; the 2026 candidate files name every candidate.
"""

import functools
import json
import re
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.fr.names import person_name

SOURCE = "fr.metropole_lyon"
# France keeps the id its polity was first loaded under, which is not derived from its ISO code.
FRANCE = "01a13b8a-b1bc-7045-aa86-d470ebdf31b2"
METROPOLIS = str(uuid5(package.NAMESPACE, "polity\x1fFR-69M"))
NATION = "01a13b8a-b1cf-7371-8194-5f2639c265b0"
SCHEME = "fr.circonscription_metropolitaine"
# The Métropole itself, a territorial collectivity with departmental powers; INSEE's code for it is 69M.
WHOLE_SCHEME = "fr.metropole"
WHOLE_CODE = "69M"
SIREN = "200046977"
PAGES = "https://www.resultats-elections.interieur.gouv.fr/metropolitaines2026/ensemble_geographique/84/69M"
GRANDLYON = (
    "https://data.grandlyon.com/geoserver/metropole-de-lyon/ows?SERVICE=WFS&VERSION=2.0.0&request=GetFeature"
    "&typename=metropole-de-lyon:adr_voie_lieu.circonscription_metropolitaine&outputFormat=application/json"
    "&SRSNAME=EPSG:4326"
)
DG = "https://static.data.gouv.fr/resources/"
FILES = {
    (2020, 1): {
        "constituency": DG + "resultats-du-premier-tour-des-elections-a-la-metropole-de-lyon-15-mars-2020/"
        "20200318-195733/resultats-par-niveau-cirmp-t1-dpt-69-2020-03-18-.xlsx",
        "station": DG + "resultats-du-premier-tour-des-elections-a-la-metropole-de-lyon-15-mars-2020/"
        "20200318-195732/resultats-par-niveau-burvot-t1-dpt-69-2020-03-18-.xlsx",
    },
    (2020, 2): {
        "constituency": DG + "resultats-du-second-tour-des-elections-a-la-metropole-de-lyon-28-juin-2020/"
        "20200629-193338/resultats-par-niveau-cirmp-2020-06-29.xlsx",
        "station": DG + "resultats-du-second-tour-des-elections-a-la-metropole-de-lyon-28-juin-2020/"
        "20200629-193314/resultats-par-niveau-burvot-t2-2020-06-29.xlsx",
    },
    (2026, 1): {
        "station": DG + "elections-metropolitaines-de-lyon-2026-resultats-du-premier-tour/20260318-182245/"
        "metropolitaines-2026-resultats-bvcommunescirconscriptions-metropolitaines-2026-03-18-18h18.csv",
        "candidates": DG + "elections-metropolitaines-de-lyon-2026-listes-candidates-au-premier-tour/"
        "20260313-151308/metropolitaines-2026-candidatures-france-entiere-tour-1-2026-03-13.csv",
        "elected": DG + "elections-metropolitaines-de-lyon-2026-resultats-du-premier-tour/20260318-182226/"
        "metropolitaines-2026-candidats-elus-france-entiere-tour-1-2026-03-18.csv",
    },
    (2026, 2): {
        "station": DG + "elections-metropolitaines-de-lyon-2026-resultats-du-second-tour/20260323-180926/"
        "metropolitaines-2026-resultats-circonscriptions-metropolitaines-par-bv-2026-03-23-19h07-1-.csv",
        "candidates": DG + "elections-metropolitaines-2026-listes-candidates-au-second-tour/20260318-190142/"
        "metropolitaines-2026-candidatures-france-entiere-tour-2-2026-03-18.csv",
        "elected": DG + "elections-metropolitaines-de-lyon-2026-resultats-du-second-tour/20260323-181026/"
        "metropolitaines-2026-candidats-elus-france-entiere-tour-2-2026-03-23-19h05.csv",
    },
}
DAYS = {
    2020: {1: date(2020, 3, 15), 2: date(2020, 6, 28)},
    2026: {1: date(2026, 3, 15), 2: date(2026, 3, 22)},
}
# The area each commune already has in the archive: the Interior Ministry's commune list, and for Pierre-Bénite
# (merged into Oullins-Pierre-Bénite in 2026) the municipal elections' record.
COMMUNES = {
    "69003": "01a13b8a-b223-7579-a39e-74c28b1c26e5",
    "69029": "01a13b8a-b223-7579-a39e-74d9e13d22cd",
    "69033": "01a13b8a-b223-7579-a39e-74dd09093791",
    "69034": "01a13b8a-b223-7579-a39e-74deb4bd1c85",
    "69040": "01a13b8a-b223-7579-a39e-74e4cda64d9b",
    "69044": "01a13b8a-b223-7579-a39e-74e794b21836",
    "69046": "01a13b8a-b223-7579-a39e-74e954a501f1",
    "69063": "01a13b8a-b223-7579-a39e-74f9359766bb",
    "69068": "01a13b8a-b223-7579-a39e-74febdc2be8c",
    "69069": "01a13b8a-b223-7579-a39e-74ff743305cc",
    "69071": "01a13b8a-b223-7579-a39e-750199b5a6a8",
    "69072": "01a13b8a-b223-7579-a39e-75020920aa52",
    "69081": "01a13b8a-b223-7579-a39e-7509a5a65c23",
    "69085": "01a13b8a-b223-7579-a39e-750dc459fb58",
    "69087": "01a13b8a-b223-7579-a39e-750f3ccfd9e1",
    "69088": "01a13b8a-b223-7579-a39e-751075f50924",
    "69089": "01a13b8a-b223-7579-a39e-75112db3ff58",
    "69091": "01a13b8a-b223-7579-a39e-7513263f2360",
    "69096": "01a13b8a-b223-7579-a39e-75182ac0c4e6",
    "69100": "01a13b8a-b223-7579-a39e-751ca8e6d9d1",
    "69116": "01a13b8a-b223-7579-a39e-752ac3338e79",
    "69117": "01a13b8a-b223-7579-a39e-752b72bee857",
    "69123": "01a13b8a-b223-7579-a39e-753149d32587",
    "69127": "01a13b8a-b223-7579-a39e-7535f68a6ac5",
    "69142": "01a13b8a-b223-7579-a39e-7542c5e34f36",
    "69143": "01a13b8a-b223-7579-a39e-75435a280148",
    "69149": "01a13b8a-b223-7579-a39e-754652286d63",
    "69152": "9d725a5d-fa70-50f1-a4d6-8b2b8d89075f",
    "69153": "01a13b8a-b223-7579-a39e-7548230a0f24",
    "69163": "01a13b8a-b223-7579-a39e-75516ce303a8",
    "69168": "01a13b8a-b223-7579-a39e-7556483e79ce",
    "69191": "01a13b8a-b223-7579-a39e-756c08ee1801",
    "69194": "01a13b8a-b223-7579-a39e-756f574f7e31",
    "69199": "01a13b8a-b223-7579-a39e-7573caa18ba3",
    "69202": "01a13b8a-b223-7579-a39e-7576cb3d6742",
    "69204": "01a13b8a-b223-7579-a39e-757804764815",
    "69205": "01a13b8a-b223-7579-a39e-7579d9a1d9b4",
    "69207": "01a13b8a-b223-7579-a39e-757bc3c8fce7",
    "69233": "01a13b8a-b223-7579-a39e-758cbc59aef1",
    "69244": "01a13b8a-b223-7579-a39e-7596a746a941",
    "69250": "01a13b8a-b223-7579-a39e-759b82524793",
    "69256": "01a13b8a-b223-7579-a39e-75a0f46effa6",
    "69259": "01a13b8a-b223-7579-a39e-75a36de5636d",
    "69260": "01a13b8a-b223-7579-a39e-75a4772a3b85",
    "69266": "01a13b8a-b223-7579-a39e-75a9191c4420",
    "69271": "01a13b8a-b223-7579-a39e-75ae579d00ca",
    "69273": "01a13b8a-b223-7579-a39e-75b0dd8c8a69",
    "69275": "01a13b8a-b223-7579-a39e-75b1281a54bf",
    "69276": "01a13b8a-b223-7579-a39e-75b20004ba79",
    "69278": "01a13b8a-b223-7579-a39e-75b40286a7dc",
    "69279": "01a13b8a-b223-7579-a39e-75b5a3a710da",
    "69282": "01a13b8a-b223-7579-a39e-75b8fb212179",
    "69283": "01a13b8a-b223-7579-a39e-75b99a9a2c29",
    "69284": "01a13b8a-b223-7579-a39e-75ba50068104",
    "69286": "01a13b8a-b223-7579-a39e-75bc34543088",
    "69290": "01a13b8a-b223-7579-a39e-75c09192fcfe",
    "69292": "01a13b8a-b223-7579-a39e-75c201d74ef7",
    "69293": "01a13b8a-b223-7579-a39e-75c3a1c3536c",
    "69296": "01a13b8a-b223-7579-a39e-75c662583e8e",
}
NUANCES = {
    "LCOM": "Liste du Parti communiste français",
    "LDIV": "Liste divers",
    "LDVC": "Liste divers centre",
    "LDVD": "Liste divers droite",
    "LDVG": "Liste divers gauche",
    "LECO": "Liste écologiste",
    "LEXD": "Liste d'extrême droite",
    "LEXG": "Liste d'extrême gauche",
    "LFI": "Liste La France insoumise",
    "LLR": "Liste Les Républicains",
    "LMDM": "Liste MoDem",
    "LREC": "Liste Reconquête",
    "LREG": "Liste régionaliste",
    "LRN": "Liste du Rassemblement national",
    "LSOC": "Liste du Parti socialiste",
    "LUC": "Liste d'union du centre",
    "LUD": "Liste d'union de la droite",
    "LUG": "Liste d'union de la gauche",
    "LVEC": "Liste Europe Écologie-Les Verts",
}
RULES = {
    "formula": "dhondt",
    "tieBreak": "votes",
    "threshold": {"percent": 5, "level": "list", "scope": "contest"},
    "lists": {"order": "closed", "exhausted": "vacant", "vacancy": "next_on_list"},
    "bonus": {
        "level": "list",
        "share": {"percent": 50, "strict": True, "rounds": [1]},
        "size": {"kind": "fraction", "fraction": 0.25, "rounding": "up"},
    },
}
METHOD = (
    "Each constituency elects its councillors by two-round list voting. In the first round, a list with more than "
    "half of the valid votes receives a quarter of the seats (rounded up), and the remaining seats go to all lists "
    "with at least 5% of the valid votes, the winner included, by the highest-average method. Otherwise a second "
    "round is held between the lists with at least 10% of the first-round valid votes, which lists with at least 5% "
    "may join; its plurality winner receives the quarter bonus, and the rest is divided in the same way."
)
STATION_NOTE = (
    "Counts by polling station. Their sums are the constituency totals, which the constituency result set holds as "
    "the official outcome."
)
ACCOUNTING = ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"), (6, "blank"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "interieur")
GEO_SOURCE = key("source", "grandlyon")
WHOLE = key("area", WHOLE_SCHEME, WHOLE_CODE)
SYSTEM = key("electoral_system", "metropole", 2020)
BODY = key("body", "council")


def area(code: str) -> str:
    return key("area", SCHEME, code)


def party(code: str) -> str:
    return key("party", code)


def constituency(interior: str) -> str:
    """The Métropole's constituency code, `MPLYO-01`, for the Interior Ministry's `69001`."""
    return f"MPLYO-{int(interior[-2:]):02d}"


def page(n: int) -> str:
    return f"{PAGES}/690{n:02d}/index.html"


def fetch() -> None:
    for files in FILES.values():
        for url in files.values():
            raw.fetch(SOURCE, url, suffix=f".{url.rsplit('.', 1)[1]}")
    raw.fetch(SOURCE, GRANDLYON, suffix=".json")
    for n in range(1, 15):
        raw.fetch(SOURCE, page(n), suffix=".html")


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", [f"{SOURCE}/{name}" for name in ("geo", "registry", "2020", "2026")])


@dataclass
class Count:
    registered: int
    cast: int
    blank: int
    invalid: int
    valid: int


@dataclass
class Entry:
    panel: int
    nuance: str
    surname: str
    forename: str
    label: str
    votes: int
    seats: int = 0


@dataclass
class Station:
    constituency: str
    commune: str
    bureau: str
    count: Count
    votes: dict[int, int]


@dataclass
class Round:
    totals: dict[str, tuple[Count, list[Entry]]] = field(default_factory=dict)
    stations: list[Station] = field(default_factory=list)
    candidates: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    elected: list[dict[str, str]] = field(default_factory=list)
    sex: dict[tuple[str, str, str], str] = field(default_factory=dict)


def number(value: object) -> int:
    return int(float(str(value))) if value is not None and str(value).strip() else 0


def _csv(url: str) -> list[dict[str, str]]:
    frame = pl.read_csv(raw.cached(SOURCE, url).path, separator=";", infer_schema_length=0, null_values=[""])
    return [{k: "" if v is None else str(v) for k, v in row.items()} for row in frame.iter_rows(named=True)]


def _sheet(url: str) -> list[tuple[object, ...]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    return book.load_sheet_by_idx(0, header_row=None).to_polars().rows()[1:]


def _entries(row: tuple[object, ...], start: int, seats: bool) -> list[Entry]:
    width = 10 if seats else 9
    out: list[Entry] = []
    for at in range(start, len(row), width):
        group = row[at : at + width]
        if group[0] is None or not str(group[0]).strip():
            continue
        out.append(
            Entry(
                panel=number(group[0]),
                nuance=str(group[1]),
                surname=str(group[3]).strip(),
                forename=str(group[4]).strip(),
                label=str(group[5]).strip(),
                seats=number(group[6]) if seats else 0,
                votes=number(group[7] if seats else group[6]),
            )
        )
    return out


def _2020(round_: int) -> Round:
    files = FILES[2020, round_]
    out = Round()
    for row in _sheet(files["constituency"]):
        count = Count(number(row[4]), number(row[7]), number(row[9]), number(row[12]), number(row[15]))
        out.totals[str(row[2])] = (count, _entries(row, 18, True))
        for entry in out.totals[str(row[2])][1]:
            out.sex[str(row[2]), entry.surname, entry.forename] = str(row[20])
    for row in _sheet(files["station"]):
        entries = _entries(row, 21, False)
        count = Count(number(row[7]), number(row[10]), number(row[12]), number(row[15]), number(row[18]))
        out.stations.append(
            Station(
                str(row[2]),
                f"69{number(row[4]):03d}",
                f"{number(row[6]):04d}",
                count,
                {entry.panel: entry.votes for entry in entries},
            )
        )
    return out


def _digits(token: str) -> int:
    return int(re.sub(r"\D", "", token))


def _page(n: int) -> dict[int, tuple[Count, list[tuple[str, str, str, int, int]]]]:
    """A 2026 constituency page: for each round it shows, the totals and each list's label, head, nuance, votes, seats."""
    text = raw.cached(SOURCE, page(n)).path.read_text()
    text = re.sub(r"<script.*?</script>", "", text[text.find("<main") :], flags=re.DOTALL)
    tokens = [t for t in (t.strip() for t in re.sub(r"<[^>]+>", "|", text).split("|")) if t]
    found: dict[int, tuple[Count, list[tuple[str, str, str, int, int]]]] = {}
    for at, token in enumerate(tokens):
        if token != "Liste des candidatures":
            continue
        round_ = next(int(m[1]) for t in reversed(tokens[:at]) if (m := re.fullmatch(r"au (\d)", t)))
        at += 7
        lists: list[tuple[str, str, str, int, int]] = []
        while not tokens[at].startswith("Mentions"):
            label, head, nuance, votes, _, _, seats = tokens[at : at + 7]
            lists.append((label, head, nuance, _digits(votes), _digits(seats)))
            at += 7
        figures = {
            label: _digits(tokens[tokens.index(label, at) + 1])
            for label in ("Inscrits", "Votants", "Blancs", "Nuls", "Exprimés")
        }
        found[round_] = (
            Count(figures["Inscrits"], figures["Votants"], figures["Blancs"], figures["Nuls"], figures["Exprimés"]),
            lists,
        )
    return found


def _2026(round_: int) -> Round:
    files = FILES[2026, round_]
    out = Round()
    for row in _csv(files["candidates"]):
        code = constituency(row["Code circonscription"])
        out.candidates.setdefault(code, []).append(row)
        out.sex[code, row["Nom sur le bulletin de vote"], row["Prénom sur le bulletin de vote"]] = row["Sexe"]
    out.elected = _csv(files["elected"])
    for row in _csv(files["station"]):
        votes = {
            number(row[f"Numéro de panneau {i}"]): number(row[f"Voix {i}"])
            for i in range(1, 30)
            if row.get(f"Numéro de panneau {i}")
        }
        count = Count(
            number(row["Inscrits"]),
            number(row["Votants"]),
            number(row["Blancs"]),
            number(row["Nuls"]),
            number(row["Exprimés"]),
        )
        out.stations.append(
            Station(
                constituency(row["Code circonscription métropolitaine"]),
                row["Code commune"],
                row["Code BV"],
                count,
                votes,
            )
        )
    return out


@functools.cache
def rounds() -> dict[tuple[int, int], Round]:
    out = {(2020, 1): _2020(1), (2020, 2): _2020(2), (2026, 1): _2026(1), (2026, 2): _2026(2)}
    for n in range(1, 15):
        code = constituency(f"690{n:02d}")
        for round_, (count, lists) in _page(n).items():
            here = out[2026, round_]
            heads = {
                f"{row['Prénom sur le bulletin de vote']} {row['Nom sur le bulletin de vote']}".lower(): row
                for row in here.candidates.get(code, [])
                if row["Tête de liste"] == "OUI"
            }
            entries: list[Entry] = []
            for label, head, nuance, votes, seats in lists:
                row = heads[head.split(" ", 1)[1].lower()]
                entries.append(
                    Entry(
                        int(row["Numéro de panneau"]),
                        nuance,
                        row["Nom sur le bulletin de vote"],
                        row["Prénom sur le bulletin de vote"],
                        label,
                        votes,
                        seats,
                    )
                )
            here.totals[code] = (count, entries)
    return out


def _nuances() -> dict[str, str]:
    names = dict(NUANCES)
    used: set[str] = set()
    for round_ in rounds().values():
        for _, entries in round_.totals.values():
            used |= {entry.nuance for entry in entries}
        for rows in round_.candidates.values():
            for row in rows:
                used.add(row["Code nuance de liste"])
                names[row["Code nuance de liste"]] = row["Nuance de liste"]
    return {code: names[code] for code in sorted(used)}


def _outline(shape: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


@dataclass
class District:
    code: str
    name: str
    seats: int
    shape: shapely.Geometry


def _districts() -> list[District]:
    features = json.loads(raw.cached(SOURCE, GRANDLYON).path.read_text())["features"]
    return sorted(
        (
            District(
                str(f["properties"]["codecirconscription"]),
                str(f["properties"]["nomcirconscription"]),
                int(f["properties"]["sieges"]),
                _outline(shapely.geometry.shape(f["geometry"])),
            )
            for f in features
        ),
        key=lambda district: district.code,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Métropole de Lyon as a polity and an area, and its 14 metropolitan constituencies, with outlines from the "
        "Métropole's open data",
    )
    p.external |= {FRANCE, NATION}
    artifact = raw.cached(SOURCE, GRANDLYON)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="official_body",
        publisher="Métropole de Lyon",
        title="Circonscriptions métropolitaines de la Métropole de Lyon",
        url="https://www.data.gouv.fr/datasets/circonscriptions-metropolitaines-de-la-metropole-de-lyon",
        license="Licence Ouverte v2.0",
        license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        attribution="Métropole de Lyon, Licence Ouverte v2.0",
    )
    p.artifact(artifact, GEO_SOURCE)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=FRANCE,
        kind="electoral_district",
        rank=2,
        noun="metropolitan constituency",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, 2020),
        scheme_code=SCHEME,
        vintage="2020",
        valid_from=date(2020, 3, 15),
        as_of=artifact.retrieved_at.date(),
    )
    p.add("polity", id=METROPOLIS, kind="county", iso_code="FR-69M")
    p.add("polity_parent", polity_id=METROPOLIS, parent_id=FRANCE, valid_from=date(2015, 1, 1))
    p.name(METROPOLIS, "Métropole de Lyon", "fr", "official")
    p.name(METROPOLIS, "Lyon Metropolis", "en", "english", False)
    p.add("external_id", entity_id=METROPOLIS, scheme="fr.siren", value=SIREN)
    p.add(
        "scheme",
        code=WHOLE_SCHEME,
        polity_id=FRANCE,
        kind="administrative",
        rank=1,
        noun="metropolis",
        code_system=None,
    )
    districts = _districts()
    # The polity holds one territory: the whole Métropole, whose outline is the union of its 14 constituencies.
    whole = _outline(shapely.union_all([district.shape for district in districts]))
    p.add("area", id=WHOLE, scheme_code=WHOLE_SCHEME)
    p.add("area_code", area_id=WHOLE, code=WHOLE_CODE)
    p.add("area_parent", area_id=WHOLE, parent_id=NATION, valid_from=date(2015, 1, 1))
    p.name(WHOLE, "Métropole de Lyon", "fr", "official")
    p.name(WHOLE, "Lyon Metropolis", "en", "english", False)
    p.add("polity_territory", polity_id=METROPOLIS, area_id=WHOLE, valid_from=date(2015, 1, 1))
    p.add(
        "area_geometry",
        area_id=WHOLE,
        valid_from=date(2015, 1, 1),
        geom=shapely.to_wkb(whole, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(whole), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=GEO_SOURCE,
    )
    for district in districts:
        code, shape = district.code, district.shape
        id = area(code)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=WHOLE)
        p.add("external_id", entity_id=id, scheme="fr.metropole_lyon.circonscription", value=f"690{int(code[-2:]):02d}")
        p.name(id, district.name, "fr", "official")
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=date(2020, 3, 15),
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=GEO_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The council of the Métropole de Lyon, its electoral system, and the list labels of its elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {FRANCE}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Ministère de l'Intérieur",
        title="Résultats des élections métropolitaines de Lyon (2020 and 2026)",
        url=f"{PAGES}/index.html",
        license="Licence Ouverte v2.0",
        license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        attribution="Ministère de l'Intérieur, Licence Ouverte v2.0",
    )
    p.add("body", id=BODY, polity_id=METROPOLIS, kind="council")
    p.name(BODY, "Council of the Métropole de Lyon", "en", "official")
    p.name(BODY, "Conseil de la Métropole de Lyon", "fr", "native", False)
    p.add("body_size", body_id=BODY, seats=150, valid_from=date(2015, 1, 1))
    p.add("electoral_system", id=SYSTEM, polity_id=FRANCE, valid_from=date(2020, 3, 1), rules=RULES, rules_version=1)
    p.name(SYSTEM, "Métropole de Lyon two-round list system", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD, lang="en")
    for code, text in _nuances().items():
        p.add("party", id=party(code), kind="nuance", polity_id=FRANCE)
        p.name(party(code), text, "fr", "official")
        p.name(party(code), code, "fr", "abbreviation", False)
    return p


def election(year: int) -> Package:
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Métropole de Lyon council election by constituency and polling station",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {FRANCE, METROPOLIS, BODY, SYSTEM, "fr.bureau"}
    first, last = DAYS[year][1], DAYS[year][2]
    for round_ in (1, 2):
        for url in FILES[year, round_].values():
            p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    if year == 2026:
        for n in range(1, 15):
            p.artifact(raw.cached(SOURCE, page(n)), SOURCE_ID)
    elections = {1: key("election", year, 1), 2: key("election", year, 2)}
    for round_, id in elections.items():
        day = DAYS[year][round_]
        p.add(
            "election",
            id=id,
            polity_id=METROPOLIS,
            parent_id=elections[1] if round_ == 2 else None,
            kind="general" if round_ == 1 else "runoff",
            date_from=day,
            date_to=day,
            status="held",
        )
        suffix = ", second round" if round_ == 2 else ""
        p.name(id, f"{year} Métropole de Lyon council election{suffix}", "en", "official")
        p.name(
            id,
            f"Élections à la Métropole de Lyon {year}{', second tour' if round_ == 2 else ''}",
            "fr",
            "native",
            False,
        )
        p.add("slug", path=f"fr/{day}-metropole-de-lyon", entity_id=id, is_current=True)
    seats = {district.code: district.seats for district in _districts()}
    names = {district.code: district.name for district in _districts()}
    persons: dict[str, dict[str, object]] = {}
    unit_ids: dict[tuple[str, str], str] = {}

    def unit(scheme: str, code: str, parent: str | None, place: str, relation: str) -> str:
        if (scheme, code) not in unit_ids:
            unit_ids[scheme, code] = key("unit", year, scheme, code)
            p.add(
                "reporting_unit",
                id=unit_ids[scheme, code],
                polity_id=METROPOLIS if scheme == SCHEME else FRANCE,
                scheme_code=scheme,
                code=code,
                parent_id=parent,
                area_relation=relation,
                area_id=place,
                kind="standard",
                valid_from=first,
                valid_to=last,
            )
        return unit_ids[scheme, code]

    contests: dict[str, str] = {}
    for round_ in (1, 2):
        here = rounds()[year, round_]
        for code in sorted(seats):
            if code not in here.totals:
                continue
            count, entries = here.totals[code]
            contest = key("contest", year, round_, code)
            p.add(
                "contest",
                id=contest,
                election_id=elections[round_],
                kind="list_allocation",
                body_id=BODY,
                district_area_id=area(code),
                magnitude=seats[code],
                round_number=round_,
                previous_round_id=contests.get(code) if round_ == 2 else None,
                electoral_system_id=SYSTEM,
                default_result_kind="counted",
            )
            contests[code] = contest
            p.name(contest, names[code], "fr", "official")
            p.add(
                "slug",
                path=f"fr/{DAYS[year][round_]}-metropole-de-lyon/{changeset.slug(names[code])}",
                entity_id=contest,
                is_current=True,
            )
            lists = {entry.panel: entry for entry in entries}
            for row in here.candidates.get(code, []):
                panel = int(row["Numéro de panneau"])
                lists.setdefault(
                    panel, Entry(panel, row["Code nuance de liste"], "", "", row["Libellé de la liste"], 0)
                )
            options = {panel: key("option", year, round_, code, panel) for panel in lists}
            for panel, entry in sorted(lists.items()):
                p.add(
                    "contest_option",
                    id=options[panel],
                    contest_id=contest,
                    kind="list",
                    party_id=party(entry.nuance),
                    ballot_position=panel,
                    ballot_label=entry.label,
                )
            candidacy_of: dict[tuple[str, str], tuple[str, str]] = {}

            def candidate(surname: str, forename: str, panel: int, order: int) -> None:
                tag = f"{year}:{code}:{surname}:{forename}"
                if tag not in persons:
                    sex = here.sex.get((code, surname, forename), "")
                    persons[tag] = {
                        "id": key("person", tag),
                        "gender": {"F": "female", "M": "male"}.get(sex),
                    }
                    p.name(str(persons[tag]["id"]), person_name(forename, surname), "fr", "official")
                id = key("candidacy", year, round_, code, panel, order)
                candidacy_of[surname, forename] = (id, options[panel])
                p.add(
                    "candidacy",
                    id=id,
                    person_id=persons[tag]["id"],
                    option_id=options[panel],
                    role="head" if order == 1 else "list_member",
                    list_position=order,
                    ballot_name=person_name(forename, surname),
                )

            if year == 2020:
                for entry in entries:
                    candidate(entry.surname, entry.forename, entry.panel, 1)
            else:
                for row in here.candidates.get(code, []):
                    candidate(
                        row["Nom sur le bulletin de vote"],
                        row["Prénom sur le bulletin de vote"],
                        int(row["Numéro de panneau"]),
                        int(row["Ordre"]),
                    )
            coarse = key("result_set", year, round_, code, "constituency")
            fine = key("result_set", year, round_, code, "station")
            stations = [s for s in here.stations if s.constituency == code]
            station_valid = sum(s.count.valid for s in stations)
            for id, is_coarse in ((coarse, True), (fine, False)):
                p.add(
                    "result_set",
                    id=id,
                    election_id=elections[round_],
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=SOURCE_ID,
                    status="active",
                    is_default=is_coarse,
                    completeness=1.0 if is_coarse else station_valid / count.valid,
                )
            p.add("note", id=key("note", fine), entity_id=fine, kind="methodology", body_md=STATION_NOTE, lang="en")
            for entry in entries:
                if entry.seats:
                    p.add(
                        "outcome", result_set_id=coarse, option_id=options[entry.panel], kind="won", seats=entry.seats
                    )
            for row in here.elected if year == 2026 else []:
                if constituency(row["CODCIRMET"]) != code:
                    continue
                id, option = candidacy_of[row["NOMPSN"], row["PREPSN"]]
                person = persons[f"{year}:{code}:{row['NOMPSN']}:{row['PREPSN']}"]
                person["birth_date"], person["birth_precision"] = date.fromisoformat(row["DATNAIPSN"]), "day"
                p.add("outcome", result_set_id=coarse, option_id=option, candidacy_id=id, kind="elected")
            area_unit = unit(SCHEME, code, None, area(code), "equals")
            votes = {entry.panel: entry.votes for entry in entries}
            _values(p, coarse, area_unit, count, votes, options)
            for measure, name in ACCOUNTING:
                p.add("declared", result_set_id=fine, option_id=None, measure=measure, value=getattr(count, name))
            for panel, value in votes.items():
                p.add("declared", result_set_id=fine, option_id=options[panel], measure=1, value=value)
            for station in stations:
                place = COMMUNES[station.commune]
                p.external.add(place)
                id = unit("fr.bureau", f"{station.commune}:{station.bureau}", area_unit, place, "within")
                _values(p, fine, id, station.count, station.votes, options)
    for row in persons.values():
        p.add("person", **row)
    return p


def _values(
    p: Package, result_set: str, unit: str, count: Count, votes: dict[int, int], options: dict[int, str]
) -> None:
    for measure, name in ACCOUNTING:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=getattr(count, name),
        )
    for panel, value in votes.items():
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=options[panel],
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=value,
        )
