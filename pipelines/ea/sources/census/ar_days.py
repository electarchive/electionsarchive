"""Argentina's provincial and municipal election days for `census/ar.json`.

The provincial days come from the Spanish Wikipedia year pages "Elecciones provinciales de Argentina de <year>",
whose Cronograma table gives, for each of the 24 jurisdictions, the day it voted and the governor, lower chamber, and
Senate seats it filled. The 1983, 1985, and 1993 tables carry no date column; each page's lead gives the one day. The
Dirección Nacional Electoral's reports confirm the 2025 days and give the 2024 to 2026 municipal days. Run
`uv run python -m ea.sources.census.ar_days` to rewrite those classes' dated rows from the raw cache.
"""

import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from urllib.parse import quote

from ea import raw
from ea.sources.census import FOLDER

SOURCE = "ar.wp_provinciales"
WIKI = "https://es.wikipedia.org/w/index.php?title={title}&action=raw"
PAGE = "https://es.wikipedia.org/wiki/{title}"
MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "enero",
            "febrero",
            "marzo",
            "abril",
            "mayo",
            "junio",
            "julio",
            "agosto",
            "septiembre",
            "octubre",
            "noviembre",
            "diciembre",
        ],
        1,
    )
} | {"setiembre": 9}
MONTH_NAMES = {number: name for name, number in MONTHS.items() if name != "setiembre"}
YEARS = [*range(1983, 2026, 2), 1986, 1990, 1994, 1998, 2006]
LETTERS = {
    "buenos aires": "B",
    "capital federal": "C",
    "cf": "C",
    "caba": "C",
    "ciudad de buenos aires": "C",
    "catamarca": "K",
    "chaco": "H",
    "chubut": "U",
    "cordoba": "X",
    "corrientes": "W",
    "entre rios": "E",
    "formosa": "P",
    "jujuy": "Y",
    "la pampa": "L",
    "la rioja": "F",
    "mendoza": "M",
    "misiones": "N",
    "neuquen": "Q",
    "rio negro": "R",
    "salta": "A",
    "san juan": "J",
    "san luis": "D",
    "santa cruz": "Z",
    "santa fe": "S",
    "santiago del estero": "G",
    "tierra del fuego": "V",
    "tucuman": "T",
}
NAMES = {
    "B": "Buenos Aires",
    "C": "Ciudad de Buenos Aires",
    "K": "Catamarca",
    "H": "Chaco",
    "U": "Chubut",
    "X": "Córdoba",
    "W": "Corrientes",
    "E": "Entre Ríos",
    "P": "Formosa",
    "Y": "Jujuy",
    "L": "La Pampa",
    "F": "La Rioja",
    "M": "Mendoza",
    "N": "Misiones",
    "Q": "Neuquén",
    "R": "Río Negro",
    "A": "Salta",
    "J": "San Juan",
    "D": "San Luis",
    "Z": "Santa Cruz",
    "S": "Santa Fe",
    "G": "Santiago del Estero",
    "V": "Tierra del Fuego",
    "T": "Tucumán",
}
# Years whose table has no date column: the one day each page's lead gives, with the phrase quoted.
LEAD_DAYS = {
    1983: ("1983-10-30", "tuvieron lugar el 30 de octubre del mencionado año"),
    1985: (
        "1985-11-03",
        (
            "tuvieron lugar el domingo 3 de noviembre del mencionado año, en dieciocho de los veinticuatro distritos"
            " electorales"
        ),
    ),
    1993: (
        "1993-10-03",
        "tuvieron lugar el domingo 3 de octubre del mencionado año, en 15 de los 24 distritos electorales",
    ),
}
# The City had no legislature or governor before its autonomy (the Concejo Deliberante was a municipal council), and
# Tierra del Fuego was a national territory until 1990.
FIRST = {"C": 1996, "V": 1991}
OVERRIDES = [
    # (class, date, province, source tag, quote)
    ("gobernador", "1996-06-30", "C", "dec50_96", "proceda a elegir UN (1) Jefe de Gobierno"),
    (
        "legislatura",
        "2023-07-02",
        "J",
        "dne_spp",
        (
            "San Juan, Cámara de Diputados: 36; Mandato Diputados: 4 años con renovación total al finalizar el período"
            " (Constitución provincial, Art. 132)"
        ),
    ),
]
CITY = {
    "gobernador": ("2000-05-07", "tuvieron lugar el domingo 7 de mayo del mencionado año con el objetivo de elegir al"),
    "legislatura": ("2000-05-07", "renovar las 60 bancas de la Legislatura Porteña"),
}


LOCAL_SOURCES = {
    "dne_pe_2024_riocuarto": (
        "Informe electoral: elecciones extraordinarias municipales, Río Cuarto (Córdoba), 23 de junio de 2024",
        "2024/08/post_electoral_-_rio_cuarto_-_cordoba.pdf",
    ),
    "dne_pe_2024_gdeheza": (
        "Informe electoral: elecciones extraordinarias municipales, General Deheza (Córdoba), 4 de agosto de 2024",
        "2024/08/post_electoral_-_gral._deheza_-_cordoba.pdf",
    ),
    "dne_pe_2024_armstrong": (
        "Informe: elecciones municipales, Armstrong (Santa Fe), 11 de agosto de 2024",
        "2024/08/armstrong_santa_fe_-_fusionado.pdf",
    ),
    "dne_pe_2025_jujuy": (
        "Informe post electoral: elecciones legislativas 2025, Jujuy, 11 de mayo de 2025",
        "2025/09/post_electoral_jujuy.pptx_.pdf",
    ),
    "dne_pe_2025_sanluis": (
        "Informe post electoral: elecciones legislativas y municipales 2025, San Luis, 11 de mayo de 2025",
        "2025/09/post_electoral_san_luis.pptx_1.pdf",
    ),
    "dne_pe_2025_corrientes": (
        "Informe post electoral: elecciones generales 2025, Corrientes, 31 de agosto de 2025",
        "2025/09/corrientes_post_electoral.pptx_.pdf",
    ),
    "dne_pe_2025_pba": (
        "Informe post electoral: elecciones legislativas 2025, Provincia de Buenos Aires, 7 de septiembre de 2025",
        "2025/09/buenos_aires_post_electoral.pptx_2.pdf",
    ),
    "dne_pe_2025_catamarca": (
        "Informe post electoral: elecciones legislativas 2025, Catamarca, 26 de octubre de 2025",
        "2025/09/catamarca.pptx_.pdf",
    ),
    "dne_pe_2025_sde": (
        "Informe post electoral: elecciones generales 2025, Santiago del Estero, 26 de octubre de 2025",
        "2025/09/santiago_del_estero.pptx_.pdf",
    ),
    "dne_pe_2025_santafe": (
        (
            "Informe post electoral: elecciones generales de convencionales reformadores y P.A.S.O. municipales y"
            " comunales 2025, Santa Fe, 13 de abril de 2025"
        ),
        "2025/09/post_electoral_-_santa_fe.pptx_.pdf",
    ),
    "dne_pe_2026_mendoza": (
        "Informe post electoral: elecciones municipales, concejales 2026, Mendoza, 22 de febrero de 2026",
        "2026/02/post_electoral_-_mendoza_municipales_2026.pptx_.pdf",
    ),
    "dne_pe_2026_sde": (
        "Informe post electoral: elecciones municipales 2026, Santiago del Estero, 2 de agosto de 2026",
        "2026/02/post_electoral_-_santiago_del_estero_municipales_2026.pptx_1.pdf",
    ),
    "dne_pe_2026_marcosjuarez": (
        "Informe post electoral: elecciones municipales 2026, Marcos Juárez (Córdoba), 6 de septiembre de 2026",
        "2026/02/post_electoral_-_marcos_juarez_municipales_2026.pptx_.pdf",
    ),
}
PBA_PASO = (
    "Simultaneidad con primarias nacionales: Cuando el Poder Ejecutivo Nacional convoca a elecciones primarias"
    " nacionales para Presidente, Vice, Diputados Nacionales, etc., la fecha de realización de las PASO provinciales"
    " se realiza el mismo día (Ley N° 14.086, Art. 2)."
)
# (class, row): dated rows from the Dirección Nacional Electoral's reports, with the report's own counts.
LOCAL_DAYS: list[tuple[str, dict[str, object]]] = [
    (
        "intendente",
        {
            "date": "2024-06-23",
            "contests": 1,
            "regions": {"X": 1},
            "source": "dne_pe_2024_riocuarto",
            "quote": "23 de junio de 2024 INFORME ELECTORAL ELECCIONES EXTRAORDINARIAS MUNICIPALES Río Cuarto – Córdoba"
            " Cargos a elegir 1 Intendente Municipal",
        },
    ),
    (
        "intendente",
        {
            "date": "2024-08-04",
            "contests": 1,
            "regions": {"X": 1},
            "source": "dne_pe_2024_gdeheza",
            "quote": "04 de agosto de 2024 INFORME ELECTORAL ELECCIONES EXTRAORDINARIAS MUNICIPALES General Deheza –"
            " Córdoba Cargos a elegir Intendente (elección extraordinaria)",
        },
    ),
    (
        "intendente",
        {
            "date": "2024-08-11",
            "contests": 1,
            "regions": {"S": 1},
            "source": "dne_pe_2024_armstrong",
            "quote": "11 de agosto de 2024 INFORME ELECCIONES MUNICIPALES Armstrong – Santa Fe Cargos a elegir Intendente",
        },
    ),
    (
        "intendente",
        {
            "date": "2025-05-11",
            "contests": 6,
            "regions": {"A": 1, "D": 5},
            "source": "dne_pe_2025_sanluis",
            "quote": "11 de mayo de 2025 INFORME POST ELECTORAL Elecciones Legislativas y Municipales 2025 San Luis"
            " Cargos que se eligieron: 4 senadores 22 diputados 5 Intendentes Municipales",
            "note": "San Luis 5. Salta's one, the municipality of Aguas Blancas, is from the Bancas en juego 2025 report:"
            " «se votarán ... el Intendente del municipio de Aguas Blancas, en el departamento de Orán».",
        },
    ),
    (
        "intendente",
        {
            "date": "2025-08-31",
            "contests": 73,
            "regions": {"W": 73},
            "source": "dne_pe_2025_corrientes",
            "quote": "31 de agosto de 2025 INFORME POST ELECTORAL Elecciones Generales 2025 Corrientes Cargos que se"
            " eligieron: 1 gobernador y vicegobernador 5 senadores 15 diputados 73 intendentes y Concejos Deliberantes",
        },
    ),
    (
        "intendente",
        {
            "date": "2025-10-26",
            "contests": 2,
            "regions": {"G": 2},
            "source": "dne_pe_2025_sde",
            "quote": "26 de octubre de 2025 INFORME POST ELECTORAL Elecciones Generales 2025 Santiago del Estero Cargos que"
            " se eligieron: 1 gobernador y vicegobernador 40 diputados provinciales 163 comisionados municipales"
            " 2 intendentes",
        },
    ),
    (
        "intendente",
        {
            "date": "2026-08-02",
            "contests": 25,
            "regions": {"G": 25},
            "source": "dne_pe_2026_sde",
            "quote": "02 de agosto de 2026 INFORME POST ELECTORAL Elecciones Municipales - 2026 Santiago del Estero Cargos"
            " que se eligieron: 25 intendentes Sus Concejos Deliberantes 1 Comisionado Municipal",
        },
    ),
    (
        "intendente",
        {
            "date": "2026-09-06",
            "contests": 1,
            "regions": {"X": 1},
            "source": "dne_pe_2026_marcosjuarez",
            "quote": "06 de septiembre de 2026 INFORME POST ELECTORAL Elecciones Municipales - 2026 Marcos Juárez –"
            " Córdoba Cargos que se eligieron: 1 intendente 9 Concejales 3 Miembros del Tribunal de Cuentas",
        },
    ),
    (
        "concejo",
        {
            "date": "2024-06-23",
            "contests": 1,
            "seats": 19,
            "regions": {"X": 1},
            "source": "dne_pe_2024_riocuarto",
            "quote": "23 de junio de 2024 INFORME ELECTORAL ELECCIONES EXTRAORDINARIAS MUNICIPALES Río Cuarto – Córdoba"
            " Cargos a elegir 1 Intendente Municipal 19 Concejales 4 integrantes del Tribunal de Cuentas",
        },
    ),
    (
        "concejo",
        {
            "date": "2025-05-11",
            "seats": 276,
            "source": "dne_pe_2025_jujuy",
            "quote": "11 de mayo de 2025 INFORME POST ELECTORAL Elecciones Legislativas 2025 Jujuy Cargos que se eligieron:"
            " 24 legisladores 155 concejales",
            "note": "Jujuy 155 and Salta 121 (Bancas en juego 2025: «121 concejales municipales distribuidos en más de 20"
            " localidades»). San Luis also renewed half of its municipal councils, a count the reports do not give.",
        },
    ),
    (
        "concejo",
        {
            "date": "2025-08-31",
            "contests": 73,
            "regions": {"W": 73},
            "source": "dne_pe_2025_corrientes",
            "quote": "31 de agosto de 2025 INFORME POST ELECTORAL Elecciones Generales 2025 Corrientes Cargos que se"
            " eligieron: 1 gobernador y vicegobernador 5 senadores 15 diputados 73 intendentes y Concejos Deliberantes",
        },
    ),
    (
        "concejo",
        {
            "date": "2025-09-07",
            "seats": 1097,
            "source": "dne_pe_2025_pba",
            "quote": "7 de septiembre de 2025 INFORME POST ELECTORAL Elecciones Legislativas 2025 Provincia de Buenos Aires"
            " Cargos que se eligieron: 23 senadores 46 diputados 1097 concejales 401 consejeros escolares",
        },
    ),
    (
        "concejo",
        {
            "date": "2025-10-26",
            "seats": 48,
            "source": "dne_pe_2025_catamarca",
            "quote": "26 de octubre de 2025 INFORME POST ELECTORAL Elecciones legislativas 2025 Catamarca Cargos que se"
            " eligieron: 21 diputados 8 senadores 2 jefes comunales 48 concejales",
        },
    ),
    (
        "concejo",
        {
            "date": "2026-02-22",
            "contests": 6,
            "seats": 33,
            "regions": {"M": 6},
            "source": "dne_pe_2026_mendoza",
            "quote": "22 de febrero de 2026 INFORME POST ELECTORAL Elecciones Municipales - Concejales 2026 Mendoza La Paz –"
            " Luján de Cuyo – Maipú – Rivadavia – Santa Rosa y San Rafael Cargos que se eligieron: 33 Concejales",
        },
    ),
    (
        "concejo",
        {
            "date": "2026-08-02",
            "contests": 25,
            "regions": {"G": 25},
            "source": "dne_pe_2026_sde",
            "quote": "02 de agosto de 2026 INFORME POST ELECTORAL Elecciones Municipales - 2026 Santiago del Estero Cargos"
            " que se eligieron: 25 intendentes Sus Concejos Deliberantes 1 Comisionado Municipal",
        },
    ),
    (
        "concejo",
        {
            "date": "2026-09-06",
            "contests": 1,
            "seats": 9,
            "regions": {"X": 1},
            "source": "dne_pe_2026_marcosjuarez",
            "quote": "06 de septiembre de 2026 INFORME POST ELECTORAL Elecciones Municipales - 2026 Marcos Juárez –"
            " Córdoba Cargos que se eligieron: 1 intendente 9 Concejales 3 Miembros del Tribunal de Cuentas",
        },
    ),
    (
        "paso_provincial",
        {
            "date": "2025-04-13",
            "regions": {"S": 1},
            "source": "dne_pe_2025_santafe",
            "quote": "13 de abril de 2025 INFORME POST ELECTORAL Elecciones Generales Convencionales Reformadores"
            " P.A.S.O. Municipales y Comunales 2025 Santa Fe",
            "note": "Municipal and communal primaries; the province held no provincial primary that year.",
        },
    ),
]
# Buenos Aires province's primaries fall on the day of the national ones (Ley 14.086, art. 2), whose days the `paso`
# class lists from the national decrees.
PBA_PASO_YEARS = ("2011-08-14", "2013-08-11", "2015-08-09", "2017-08-13", "2019-08-11", "2021-09-12", "2023-08-13")


REGIONS_NOTE = (
    "Each dated row's regions count its contests by province, keyed by the ISO 3166-2:AR letter (B Buenos Aires, C City"
    " of Buenos Aires, and so on)."
)
SPANS = {
    "intendente": [
        {
            "date": "1983-10-30",
            "through": "2023-12-31",
            "note": "Municipal elections before 2024 are not yet enumerated by province; the 24 jurisdictions set their own"
            " days, and most hold them with the provincial election.",
        }
    ],
    "concejo": [
        {
            "date": "1983-10-30",
            "through": "2023-12-31",
            "note": "Municipal elections before 2024 are not yet enumerated by province; the 24 jurisdictions set their own"
            " days, and most hold them with the provincial election.",
        }
    ],
    "paso_provincial": [
        {
            "date": "2009-12-11",
            "through": "2026-11-15",
            "note": "Buenos Aires province's primaries are listed by day; the other provinces' primaries (the City of Buenos"
            " Aires, Catamarca, Chaco, Entre Ríos, La Pampa, Mendoza, San Juan, San Luis, Santa Fe, and Salta before"
            " 2024) are not yet enumerated.",
        }
    ],
}


def pages() -> dict[int, str]:
    out = {}
    for year in YEARS:
        title = f"Elecciones_provinciales_de_Argentina_de_{year}"
        out[year] = raw.cached(SOURCE, WIKI.format(title=title)).path.read_text(encoding="utf-8")
    return out


def letter(cell: str) -> str | None:
    cell = re.sub(r"<small>.*", "", cell)
    cell = re.sub(r"rowspan=\"?\d+\"?\|?", "", cell)
    found = re.search(r"\{\{ARG\|([^}|]+)", cell) or re.search(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", cell)
    if not found:
        return None
    name = "".join(c for c in unicodedata.normalize("NFD", found.group(1).lower()) if unicodedata.category(c) != "Mn")
    name = re.sub(r"^provincia del? ", "", name.strip())
    return LETTERS.get(re.sub(r" \(argentina\)", "", name))


def span(cell: str) -> int:
    found = re.search(r"colspan=\"?(\d+)", cell)
    return int(found.group(1)) if found else 1


def day(cell: str) -> str | None:
    found = re.search(r"\{\{dts\|(\d+)\|(\w+)\|(\d{4})", cell, re.IGNORECASE)
    if not found:
        return None
    month = int(found.group(2)) if found.group(2).isdigit() else MONTHS[found.group(2).lower()]
    return date(int(found.group(3)), month, int(found.group(1))).isoformat()


def seats(cell: str | None) -> int | None:
    if cell is None or "No elige" in cell:
        return None
    found = re.match(r"\s*(\d+)", re.sub(r"<ref.*", "", cell))
    return int(found.group(1)) if found else None


def table(year: int, text: str) -> list[dict[str, object]]:
    start = text.find("== Cronograma")
    parts = re.split(r"\n\|-", text[start : text.find("\n|}", start)])
    header: list[str] = []
    for line in re.findall(r"^!\s*(.*)$", parts[1], re.MULTILINE):
        header += [re.sub(r"colspan=\"?\d+\"?\s*\|\s*", "", line).strip().lower()] * span(line)
    header = header[1:]
    rows: list[dict[str, object]] = []
    current = None
    for part in parts[2:]:
        cells: list[str] = []
        for line in part.strip("\n").split("\n"):
            if line.startswith("|"):
                cells.append(line[1:].strip())
            elif cells:
                cells[-1] += " " + line.strip()
        if not cells:
            continue
        if cells[0].startswith("{{dts"):
            province = current
        else:
            province = letter(cells[0])
            if province is None:
                continue
            cells = cells[1:]
        current = province
        expanded = [cell for cell in cells for _ in range(span(cell))]

        def column(name: str, expanded: list[str] = expanded) -> str | None:
            for index, title in enumerate(header):
                if title.startswith(name) and index < len(expanded):
                    return expanded[index]
            return None

        governor = column("gobernador")
        rows.append(
            {
                "year": year,
                "province": province,
                "date": day(expanded[0]) if expanded and "dts" in expanded[0] else None,
                "governor": governor is not None and "No elige" not in governor,
                "lower": seats(column("diputados")),
                "senate": seats(column("senadores")),
            }
        )
    return rows


def human(iso: str) -> str:
    when = date.fromisoformat(iso)
    return f"{when.day} de {MONTH_NAMES[when.month]} de {when.year}"


def build() -> dict[str, list[dict[str, object]]]:
    held: dict[str, dict[str, dict[str, int]]] = {
        key: defaultdict(dict) for key in ("gobernador", "legislatura", "senado_provincial")
    }
    years: dict[str, dict[str, int]] = {key: {} for key in held}
    for year, text in pages().items():
        for row in table(year, text):
            province = str(row["province"])
            when = row["date"] if isinstance(row["date"], str) else None
            if when is None and year in LEAD_DAYS:
                when = LEAD_DAYS[year][0]
            if when is None or year < FIRST.get(province, 0):
                continue
            for key, flag in (
                ("gobernador", row["governor"]),
                ("legislatura", row["lower"]),
                ("senado_provincial", row["senate"]),
            ):
                if flag:
                    held[key][when][province] = 1
                    years[key][when] = year
    for key, (when, _) in CITY.items():
        held[key][when]["C"] = 1
        years[key][when] = 2000
    for key, when, province, _, _ in OVERRIDES:
        held[key][when][province] = 1
        years[key][when] = int(when[:4])
    out: dict[str, list[dict[str, object]]] = {}
    for key, days in held.items():
        rows: list[dict[str, object]] = []
        for when in sorted(days):
            regions = dict(sorted(days[when].items()))
            names = "; ".join(f"{NAMES[p]}, {human(when)}" for p in regions)
            year = years[key][when]
            if over := next((o for o in OVERRIDES if o[0] == key and o[1] == when), None):
                tag, quote = over[3], over[4]
                if len(regions) > 1:
                    raise ValueError(f"{key} {when}: an override shares its day with {sorted(regions)}")
            elif when == "2000-05-07":
                tag, quote = "wp_caba_2000", CITY[key][1]
            elif year in LEAD_DAYS and when == LEAD_DAYS[year][0]:
                tag, quote = (
                    f"wp_prov_{year}",
                    f"Las elecciones provinciales de Argentina de {year} {LEAD_DAYS[year][1]}",
                )
            else:
                tag, quote = f"wp_prov_{year}", f"Cronograma de las elecciones provinciales de {year}: {names}"
            rows.append({"date": when, "contests": len(regions), "regions": regions, "source": tag, "quote": quote})
        out[key] = rows
    return out


def sources(used: set[str]) -> dict[str, dict[str, str]]:
    out = {}
    for tag in sorted(used):
        if tag.startswith("wp_prov_"):
            year = tag.removeprefix("wp_prov_")
            title = f"Elecciones provinciales de Argentina de {year}"
            out[tag] = {
                "title": title,
                "publisher": "Wikipedia (Spanish)",
                "url": PAGE.format(title=quote(title.replace(" ", "_"))),
                "kind": "community",
                "note": "The Cronograma table gives each jurisdiction's day and the governor, lower-chamber, and Senate"
                " seats it filled. Revision read 2026-11-17.",
                "license": "CC BY-SA 4.0",
            }
    for tag, (title, path) in LOCAL_SOURCES.items():
        if tag in used:
            out[tag] = {
                "title": title,
                "publisher": "Dirección Nacional Electoral, Observatorio Electoral",
                "url": f"https://www.argentina.gob.ar/sites/default/files/{path}",
                "kind": "official_body",
                "license": "CC BY 4.0",
            }
    out["wp_caba_2000"] = {
        "title": "Elecciones de la Ciudad Autónoma de Buenos Aires de 2000",
        "publisher": "Wikipedia (Spanish)",
        "url": PAGE.format(title=quote("Elecciones_de_la_Ciudad_Autónoma_de_Buenos_Aires_de_2000")),
        "kind": "community",
        "note": "Revision read 2026-11-17: the 7 May 2000 election of the Jefe de Gobierno and the 60 seats of the"
        " Legislatura, which the year's provincial table does not list.",
        "license": "CC BY-SA 4.0",
    }
    return out


def main() -> None:
    path = FOLDER / "ar.json"
    spec = json.loads(path.read_text())
    built = build()
    for key, row in LOCAL_DAYS:
        built.setdefault(key, []).append(row)
    built["paso_provincial"] += [
        {
            "date": when,
            "contests": 1,
            "regions": {"B": 1},
            "source": "dne_spp",
            "quote": PBA_PASO,
            "note": "Buenos Aires province, on the day of the national primaries.",
        }
        for when in PBA_PASO_YEARS
    ]
    for key, rows in built.items():
        rows.sort(key=lambda row: str(row["date"]))
    used = {row["source"] for rows in built.values() for row in rows}
    spec["sources"] |= sources({str(tag) for tag in used})
    for entry in spec["classes"]:
        if entry["key"] in built:
            entry["dates"] = [*SPANS.get(str(entry["key"]), []), *built[str(entry["key"])]]
            if REGIONS_NOTE not in entry["note"]:
                entry["note"] += " " + REGIONS_NOTE
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    for key, rows in built.items():
        print(key, len(rows), sum(int(str(row.get("contests") or 0)) for row in rows))


if __name__ == "__main__":
    main()
