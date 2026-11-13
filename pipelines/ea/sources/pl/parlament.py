from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "pl.parlament"
VERSION = "1"
PUBLISHER = "Kancelaria Sejmu"
LAWS = {
    "Ustawa z dnia 7 kwietnia 1989 r. o zmianie Konstytucji Polskiej Rzeczypospolitej Ludowej": (
        "https://api.sejm.gov.pl/eli/acts/DU/1989/101/text.html"
    ),
    "Konstytucja Rzeczypospolitej Polskiej z dnia 2 kwietnia 1997 r.": (
        "https://api.sejm.gov.pl/eli/acts/DU/1997/483/text.pdf"
    ),
}
NOVELIZATION = date(1989, 4, 8)
BODIES = {
    "sejm": ("lower_house", "Sejm", "Sejm Rzeczypospolitej Polskiej", 460),
    "senat": ("upper_house", "Senate", "Senat Rzeczypospolitej Polskiej", 100),
}
OFFICES = {
    "prezydent": ("head_of_state", None, "President of Poland", "Prezydent Rzeczypospolitej Polskiej"),
    "prezes-rady-ministrow": ("head_of_government", None, "Prime Minister of Poland", "Prezes Rady Ministrów"),
    "marszalek-sejmu": ("speaker", "sejm", "Marshal of the Sejm", "Marszałek Sejmu"),
    "marszalek-senatu": ("speaker", "senat", "Marshal of the Senate", "Marszałek Senatu"),
}


def fetch() -> None:
    for url in LAWS.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The Polish parliament: the Sejm and the Senat, their sizes since the April 1989 amendment, and the national offices",
            polity="PL",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        for title, url in LAWS.items():
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record: official legislation, free to reuse",
                license_url="https://isap.sejm.gov.pl/isap.nsf/home.xsp",
            )
            cs.artifact(raw.cached(SOURCE, url), source)
        poland = cs.polity("PL")
        if poland in cs.minted:
            raise ValueError("publish eu.members before pl.parlament")
        bodies: dict[str, UUID] = {}
        for key, (kind, english, native, seats) in BODIES.items():
            bodies[key] = cs.key("pl.body", key)
            cs.add("body", id=bodies[key], polity_id=poland, kind=kind)
            name(bodies[key], english)
            name(bodies[key], native, "pl", "native")
            cs.add("body_size", body_id=bodies[key], seats=seats, valid_from=NOVELIZATION)
        for key, (kind, body, english, native) in OFFICES.items():
            office = cs.key("pl.office", key)
            cs.add("office", id=office, polity_id=poland, kind=kind, body_id=None if body is None else bodies[body])
            name(office, english)
            name(office, native, "pl", "native")
        return cs.write()
