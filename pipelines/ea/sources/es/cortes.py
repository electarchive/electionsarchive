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
        for key, (kind, body, english, native) in OFFICES.items():
            office = cs.key("es.office", key)
            cs.add("office", id=office, polity_id=spain, kind=kind, body_id=None if body is None else bodies[body])
            name(office, english)
            name(office, native, "es", "native")
        return cs.write()
