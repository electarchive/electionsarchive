from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "world.countries"
VERSION = "3"
COUNTRIES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "AL": ("Q222", [("sq", "Shqipëria")]),
    "AM": ("Q399", [("hy", "Հայաստան")]),
    "AR": ("Q414", [("es", "Argentina")]),
    "AU": ("Q408", []),
    "AZ": ("Q227", [("az", "Azərbaycan")]),
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
    "SO": ("Q1045", [("so", "Soomaaliya")]),
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


POLITIES: dict[str, tuple[str, str | None, str, list[tuple[str, str]], str, str, str | None]] = {
    "CY-NC": (
        "de_facto_state",
        "CY",
        "Turkish Republic of Northern Cyprus",
        [("tr", "Kuzey Kıbrıs Türk Cumhuriyeti")],
        "1975-01-01",
        "year",
        None,
    ),
    "SO-SL": (
        "de_facto_state",
        "SO",
        "Republic of Somaliland",
        [("so", "Jamhuuriyadda Somaliland")],
        "1991-05-18",
        "day",
        None,
    ),
    "GE-AB": (
        "de_facto_state",
        "GE",
        "Republic of Abkhazia",
        [("ru", "Республика Абхазия")],
        "1992-07-23",
        "day",
        None,
    ),
    "GE-SO": (
        "de_facto_state",
        "GE",
        "Republic of South Ossetia",
        [("ru", "Республика Южная Осетия")],
        "1990-09-20",
        "day",
        None,
    ),
    "MD-SN": (
        "de_facto_state",
        "MD",
        "Pridnestrovian Moldavian Republic",
        [("ru", "Приднестровская Молдавская Республика"), ("ro", "Republica Moldovenească Nistreană")],
        "1990-09-02",
        "day",
        None,
    ),
    "AZ-NK": (
        "de_facto_state",
        "AZ",
        "Republic of Artsakh",
        [("hy", "Արցախի Հանրապետություն")],
        "1991-09-02",
        "day",
        "2024-01-01",
    ),
    "EH-SR": (
        "de_facto_state",
        None,
        "Sahrawi Arab Democratic Republic",
        [("ar", "الجمهورية العربية الصحراوية الديمقراطية")],
        "1976-02-27",
        "day",
        None,
    ),
    "SUN": (
        "historical_state",
        None,
        "Union of Soviet Socialist Republics",
        [("ru", "Союз Советских Социалистических Республик")],
        "1922-12-30",
        "day",
        "1991-12-26",
    ),
    "YUG": (
        "historical_state",
        None,
        "Yugoslavia",
        [("sh", "Socijalistička Federativna Republika Jugoslavija")],
        "1918-12-01",
        "day",
        "2003-02-04",
    ),
    "CSK": ("historical_state", None, "Czechoslovakia", [("cs", "Československo")], "1918-10-28", "day", "1993-01-01"),
    "SCG": (
        "historical_state",
        None,
        "Serbia and Montenegro",
        [("sr", "Србија и Црна Гора")],
        "2003-02-04",
        "day",
        "2006-06-03",
    ),
    "DDR": (
        "historical_state",
        None,
        "German Democratic Republic",
        [("de", "Deutsche Demokratische Republik")],
        "1949-10-07",
        "day",
        "1990-10-03",
    ),
    "ANT": (
        "historical_state",
        None,
        "Netherlands Antilles",
        [("nl", "Nederlandse Antillen")],
        "1954-12-29",
        "day",
        "2010-10-10",
    ),
    "YMD": (
        "historical_state",
        None,
        "People's Democratic Republic of Yemen",
        [("ar", "جمهورية اليمن الديمقراطية الشعبية")],
        "1967-11-30",
        "day",
        "1990-05-22",
    ),
    "RHO": ("historical_state", None, "Southern Rhodesia", [], "1923-10-01", "day", "1980-04-18"),
    "SKM": ("historical_state", None, "Kingdom of Sikkim", [], "1950-12-05", "day", "1975-04-26"),
    "VDR": (
        "historical_state",
        None,
        "Democratic Republic of Vietnam",
        [("vi", "Việt Nam Dân chủ Cộng hòa")],
        "1945-09-02",
        "day",
        "1976-07-02",
    ),
    "XOT": (
        "historical_state",
        None,
        "Ottoman Empire",
        [("tr", "Osmanlı Devleti")],
        "1299-01-01",
        "year",
        "1922-11-01",
    ),
    "XDZ": (
        "historical_state",
        None,
        "Free City of Danzig",
        [("de", "Freie Stadt Danzig")],
        "1920-11-15",
        "day",
        "1939-09-01",
    ),
    "XSV": (
        "historical_state",
        None,
        "Republic of Vietnam",
        [("vi", "Việt Nam Cộng hòa")],
        "1955-10-26",
        "day",
        "1975-04-30",
    ),
    "XAH": (
        "historical_state",
        None,
        "Austria-Hungary",
        [("de", "Österreich-Ungarn")],
        "1867-06-08",
        "day",
        "1918-11-12",
    ),
    "XWI": ("historical_state", None, "West Indies Federation", [], "1958-01-03", "day", "1962-06-01"),
    "XFR": ("historical_state", None, "Federation of Rhodesia and Nyasaland", [], "1953-08-01", "day", "1964-01-01"),
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Countries with no record in the archive yet, each with its English and native names, Wikidata "
            "item, and Natural Earth outline; de facto and former states the census world tier covers, each with its "
            "dates and recognized parent",
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
            area, label = areas[iso]
            cs.add("polity", id=polity, kind="sovereign_state", iso_code=iso)
            cs.add("name", entity_id=polity, text=label, lang="en", kind="official", is_preferred=True)
            for lang, text in natives:
                cs.add("name", entity_id=polity, text=text, lang=lang, kind="native", is_preferred=False)
            cs.add("slug", path=iso.lower(), entity_id=polity)
            cs.add("external_id", entity_id=polity, scheme="wikidata", value=wikidata)
            cs.add("polity_territory", polity_id=polity, area_id=area)
        for iso, (kind, parent, name, natives, start, precision, end) in POLITIES.items():
            polity = cs.polity(iso)
            cs.add("polity", id=polity, kind=kind, iso_code=iso)
            cs.add(
                "name",
                entity_id=polity,
                text=name,
                lang="en",
                kind="official",
                is_preferred=True,
                valid_from=start,
                valid_from_precision=precision,
                valid_to=end,
            )
            for lang, text in natives:
                cs.add("name", entity_id=polity, text=text, lang=lang, kind="native", is_preferred=False)
            cs.add("slug", path=iso.lower(), entity_id=polity)
            if parent is not None:
                cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(parent), valid_from=start, valid_to=end)
        return cs.write()
