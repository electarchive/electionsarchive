from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "world.countries"
VERSION = "1"
NAMED_BY_PACKAGES = {"AR", "CL", "PE", "ZA"}
COUNTRIES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "AL": ("Q222", [("sq", "Shqipëria")]),
    "AM": ("Q399", [("hy", "Հայաստան")]),
    "AR": ("Q414", [("es", "Argentina")]),
    "AU": ("Q408", []),
    "BA": ("Q225", [("bs", "Bosna i Hercegovina"), ("hr", "Bosna i Hercegovina"), ("sr", "Босна и Херцеговина")]),
    "BO": ("Q750", [("es", "Bolivia")]),
    "BT": ("Q917", [("dz", "འབྲུག་ཡུལ་")]),
    "BW": ("Q963", [("tn", "Botswana")]),
    "BZ": ("Q242", []),
    "CI": ("Q1008", [("fr", "Côte d’Ivoire")]),
    "CL": ("Q298", [("es", "Chile")]),
    "CO": ("Q739", [("es", "Colombia")]),
    "CR": ("Q800", [("es", "Costa Rica")]),
    "DM": ("Q784", []),
    "DO": ("Q786", [("es", "República Dominicana")]),
    "EC": ("Q736", [("es", "Ecuador")]),
    "GE": ("Q230", [("ka", "საქართველო")]),
    "GL": ("Q223", [("kl", "Kalaallit Nunaat"), ("da", "Grønland")]),
    "GT": ("Q774", [("es", "Guatemala")]),
    "GY": ("Q734", []),
    "HK": ("Q8646", [("zh", "香港")]),
    "HN": ("Q783", [("es", "Honduras")]),
    "IS": ("Q189", [("is", "Ísland")]),
    "JM": ("Q766", []),
    "KG": ("Q813", [("ky", "Кыргызстан"), ("ru", "Кыргызстан")]),
    "LI": ("Q347", [("de", "Liechtenstein")]),
    "LR": ("Q1014", []),
    "MD": ("Q217", [("ro", "Moldova")]),
    "ME": ("Q236", [("cnr", "Crna Gora")]),
    "MG": ("Q1019", [("mg", "Madagasikara"), ("fr", "Madagascar")]),
    "MK": ("Q221", [("mk", "Северна Македонија")]),
    "MM": ("Q836", [("my", "မြန်မာ")]),
    "MN": ("Q711", [("mn", "Монгол Улс")]),
    "MW": ("Q1020", []),
    "MY": ("Q833", [("ms", "Malaysia")]),
    "NA": ("Q1030", []),
    "NG": ("Q1033", []),
    "NO": ("Q20", [("nb", "Norge"), ("nn", "Noreg")]),
    "NP": ("Q837", [("ne", "नेपाल")]),
    "NZ": ("Q664", [("mi", "Aotearoa")]),
    "PA": ("Q804", [("es", "Panamá")]),
    "PE": ("Q419", [("es", "Perú")]),
    "PH": ("Q928", [("fil", "Pilipinas")]),
    "PK": ("Q843", [("ur", "پاکستان")]),
    "PS": ("Q219060", [("ar", "فلسطين")]),
    "PY": ("Q733", [("es", "Paraguay"), ("gn", "Paraguái")]),
    "RS": ("Q403", [("sr", "Србија")]),
    "SB": ("Q685", []),
    "SC": ("Q1042", [("fr", "Seychelles"), ("crs", "Sesel")]),
    "SV": ("Q792", [("es", "El Salvador")]),
    "TT": ("Q754", []),
    "TW": ("Q865", [("zh", "臺灣")]),
    "UA": ("Q212", [("uk", "Україна")]),
    "UY": ("Q77", [("es", "Uruguay")]),
    "XK": ("Q1246", [("sq", "Kosova"), ("sr", "Косово")]),
    "ZA": (
        "Q258",
        [
            ("af", "Suid-Afrika"),
            ("zu", "iNingizimu Afrika"),
            ("xh", "uMzantsi Afrika"),
            ("nso", "Afrika Borwa"),
            ("st", "Afrika Borwa"),
            ("tn", "Aforika Borwa"),
            ("ss", "Ningizimu Afrika"),
            ("ve", "Afurika Tshipembe"),
            ("ts", "Afrika Dzonga"),
            ("nr", "iSewula Afrika"),
        ],
    ),
    "ZM": ("Q953", []),
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Countries with no record in the archive yet, each with its English and native names, Wikidata "
            "item, and Natural Earth outline",
            polity=None,
            kind="edit",
        )
        areas: dict[str, tuple[UUID, str]] = {
            iso: (area, label)
            for iso, area, label in conn.execute(
                """select e.value, a.id, n.text from area a
                   join external_id e on e.entity_id = a.id and e.scheme = 'iso3166'
                   join name n on n.entity_id = a.id and n.lang = 'en' and n.is_preferred
                   where a.scheme_code = 'world.country'"""
            )
        }
        for iso, (wikidata, natives) in COUNTRIES.items():
            polity = cs.polity(iso)
            if polity not in cs.minted:
                continue
            area, label = areas[iso]
            cs.add("polity", id=polity, kind="sovereign_state", iso_code=iso)
            if iso not in NAMED_BY_PACKAGES:
                cs.add("name", entity_id=polity, text=label, lang="en", kind="official", is_preferred=True)
                for lang, text in natives:
                    cs.add("name", entity_id=polity, text=text, lang=lang, kind="native", is_preferred=False)
            cs.add("slug", path=iso.lower(), entity_id=polity)
            cs.add("external_id", entity_id=polity, scheme="wikidata", value=wikidata)
            cs.add("polity_territory", polity_id=polity, area_id=area)
        return cs.write()
