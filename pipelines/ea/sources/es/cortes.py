from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "es.cortes"
VERSION = "1"
PUBLISHER = "Agencia Estatal Boletín Oficial del Estado"
LAWS = {
    "Constitución Española": "https://www.boe.es/buscar/act.php?id=BOE-A-1978-31229",
    "Ley Orgánica 5/1985, de 19 de junio, del Régimen Electoral General": (
        "https://www.boe.es/buscar/act.php?id=BOE-A-1985-11672"
    ),
}
CONSTITUENT = date(1977, 7, 13)
SENATE_TABLE = (
    "Cuadro de evolución del número de Senadores designados por cada Comunidad Autónoma desde la I Legislatura"
)
SENATE_TABLE_URL = "https://www.senado.es/web/conocersenado/temasclave/composicionsenadoelecciones/detalle/index.html?id=CUADRO_EVOLUCION"
SENATE_TABLE_CACHE = (
    "https://web.archive.org/web/20251216122910id_/"
    "https://www.senado.es/web/conocersenado/temasclave/composicionsenadoelecciones/detalle/index.html?id=CUADRO_EVOLUCION"
)
ELECTORAL_DECREE = "Real Decreto-ley 20/1977, de 18 de marzo, sobre Normas Electorales"
ELECTORAL_DECREE_URL = "https://www.boe.es/boe/dias/1977/03/23/pdfs/A06584-06600.pdf"
ELECTED = 208
FIRST_ELECTED = 207
APPOINTED_1977 = 41
CONSTITUTED = [
    date(1979, 3, 23),
    date(1982, 11, 24),
    date(1986, 7, 15),
    date(1989, 11, 21),
    date(1993, 7, 6),
    date(1996, 4, 27),
    date(2000, 4, 5),
    date(2004, 4, 2),
    date(2008, 4, 1),
    date(2011, 12, 13),
    date(2016, 1, 13),
    date(2016, 11, 17),
    date(2019, 5, 21),
    date(2019, 12, 3),
    date(2023, 8, 17),
]
DESIGNATED = [0, 46, 46, 46, 48, 49, 51, 51, 56, 58, 58, 58, 57, 57, 58]
DESIGNATED_CHANGES = [(date(1991, 7, 1), 47), (date(1999, 7, 1), 51)]


def senate_spans() -> list[tuple[date, date | None, int, str]]:
    marks = [(day, seats, "day") for day, seats in zip(CONSTITUTED, DESIGNATED, strict=True)]
    marks += [(day, seats, "month") for day, seats in DESIGNATED_CHANGES]
    marks.sort()
    spans: list[tuple[date, date | None, int, str]] = []
    for index, (day, seats, precision) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else None
        spans.append((day, end, seats, precision))
    return spans


BODIES = {
    "congreso": ("lower_house", "Congress of Deputies", "Congreso de los Diputados"),
    "senado": ("upper_house", "Senate", "Senado"),
}
OFFICES = {
    "rey": ("head_of_state", None, "King of Spain", "Rey de España"),
    "presidente-gobierno": (
        "head_of_government",
        None,
        "President of the Government of Spain",
        "Presidente del Gobierno",
    ),
    "presidente-congreso": (
        "speaker",
        "congreso",
        "President of the Congress of Deputies",
        "Presidente del Congreso de los Diputados",
    ),
    "presidente-senado": ("speaker", "senado", "President of the Senate", "Presidente del Senado"),
}


def fetch() -> None:
    for url in LAWS.values():
        raw.fetch(SOURCE, url, suffix=".html")
    raw.fetch(SOURCE, ELECTORAL_DECREE_URL, suffix=".pdf")
    raw.fetch(SOURCE, SENATE_TABLE_CACHE, suffix=".html")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The Cortes Generales of Spain: the Congreso and the Senado, the Congreso's size, and the national offices",
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        sources: dict[str, UUID] = {}
        for title, url in LAWS.items():
            sources[title] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[title],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record: official legislation, free to reuse",
                license_url="https://www.boe.es/informacion/aviso_legal/index.php",
            )
            cs.artifact(raw.cached(SOURCE, url), sources[title])
        spain = cs.polity("ES")
        if spain in cs.minted:
            raise ValueError("publish eu.members before es.cortes")
        bodies: dict[str, UUID] = {}
        for key, (kind, english, native) in BODIES.items():
            bodies[key] = cs.key("es.body", key)
            cs.add("body", id=bodies[key], polity_id=spain, kind=kind)
            name(bodies[key], english)
            name(bodies[key], native, "es", "native")
        cs.add("body_size", body_id=bodies["congreso"], seats=350, valid_from=CONSTITUENT)
        decree = cs.source(PUBLISHER, ELECTORAL_DECREE)
        cs.add(
            "source",
            id=decree,
            kind="official_body",
            publisher=PUBLISHER,
            title=ELECTORAL_DECREE,
            url=ELECTORAL_DECREE_URL,
            license="Public record: official legislation, free to reuse",
            license_url="https://www.boe.es/informacion/aviso_legal/index.php",
        )
        cs.artifact(raw.cached(SOURCE, ELECTORAL_DECREE_URL), decree)
        table = cs.source("Senado de España", SENATE_TABLE)
        cs.add(
            "source",
            id=table,
            kind="official_body",
            publisher="Senado de España",
            title=SENATE_TABLE,
            url=SENATE_TABLE_URL,
            license="Public record: official parliamentary information",
            license_url="https://www.senado.es/web/aviso_legal/index.html",
        )
        cs.artifact(raw.cached(SOURCE, SENATE_TABLE_CACHE), table)
        senado = bodies["senado"]
        cs.add(
            "body_size",
            body_id=senado,
            seats=FIRST_ELECTED + APPOINTED_1977,
            valid_from=CONSTITUENT,
            valid_to=CONSTITUTED[0],
        )
        cs.add(
            "body_size_part",
            body_id=senado,
            category="elected",
            seats=FIRST_ELECTED,
            valid_from=CONSTITUENT,
            valid_to=CONSTITUTED[0],
        )
        cs.add(
            "body_size_part",
            body_id=senado,
            category="appointed",
            seats=APPOINTED_1977,
            valid_from=CONSTITUENT,
            valid_to=CONSTITUTED[0],
        )
        cs.add("body_size_part", body_id=senado, category="elected", seats=ELECTED, valid_from=CONSTITUTED[0])
        for start, end, designated, precision in senate_spans():
            cs.add(
                "body_size",
                body_id=senado,
                seats=ELECTED + designated,
                valid_from=start,
                valid_to=end,
                valid_from_precision=precision,
                valid_to_precision=precision,
            )
            if designated:
                cs.add(
                    "body_size_part",
                    body_id=senado,
                    category="indirect",
                    seats=designated,
                    valid_from=start,
                    valid_to=end,
                    valid_from_precision=precision,
                    valid_to_precision=precision,
                )
        for key, (kind, body, english, native) in OFFICES.items():
            office = cs.key("es.office", key)
            cs.add("office", id=office, polity_id=spain, kind=kind, body_id=None if body is None else bodies[body])
            name(office, english)
            name(office, native, "es", "native")
        return cs.write()
