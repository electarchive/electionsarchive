from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "it.lineage"
VERSION = "1"
API = "https://it.wikipedia.org/w/api.php?"
LEGA = "Lega per Salvini Premier"
LIST = "Partito Democratico - Italia Democratica e Progressista"
GENERAL = date(2022, 9, 25)
LEGA_NORD = UUID("3955e9a5-c36d-5457-bb55-ed5ffc41eed8")
LEGA_PER_SALVINI = UUID("01a13bf7-300c-7784-acd9-f60cef597cca")
LIST_VEHICLE = UUID("01a13bf7-300c-7784-acd9-f60a030c0788")
PD = UUID("e2dd275d-8eff-5620-8364-588dd9a80a10")
MEMBERS = {
    "Articolo Uno": "Articolo Uno",
    "Partito Socialista Italiano": "Italian Socialist Party (2007)",
    "Democrazia Solidale": "Solidarity Democracy",
    "Centristi per l'Europa": "Centrists for Europe",
    "Movimento Repubblicani Europei": "European Republican Movement",
    "Volt Italia": "Volt Italy",
    "Emilia-Romagna Coraggiosa": "Courageous Emilia-Romagna",
}


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "formatversion": 2, "redirects": 1}
    )


def fetch() -> None:
    for title in (LEGA, LIST):
        raw.fetch(SOURCE, page(title), suffix=".json")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Italian party lineage: Lega Nord to Lega per Salvini Premier, and the 2022 PD–Italia Democratica e Progressista joint list",
            polity="IT",
        )
        sources = {}
        for title in (LEGA, LIST):
            sources[title] = cs.source("Wikipedia (it)", title)
            cs.add(
                "source",
                id=sources[title],
                kind="community",
                publisher="Wikipedia (it)",
                title=title,
                url=f"https://it.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                license="CC BY-SA 4.0",
            )
            cs.artifact(raw.cached(SOURCE, page(title)), sources[title])
        italy = cs.polity("IT")
        general = cs.key("it.election", f"{GENERAL}:general")
        if general in cs.minted:
            raise ValueError("publish it.interno before it.lineage")
        cs.add(
            "party_relation",
            from_party_id=LEGA_PER_SALVINI,
            to_party_id=LEGA_NORD,
            kind="successor_of",
            date=date(2017, 12, 1),
            date_precision="month",
            source_id=sources[LEGA],
        )
        cs.add(
            "party_relation",
            from_party_id=LIST_VEHICLE,
            to_party_id=PD,
            kind="list_vehicle_for",
            election_id=general,
            source_id=sources[LIST],
        )
        alliance = cs.key("it.alliance", f"{GENERAL}:{slug(LIST)}")
        cs.add("alliance", id=alliance, kind="joint_list", election_id=general)
        cs.add(
            "name",
            entity_id=alliance,
            text="Democratic Party – Democratic and Progressive Italy",
            lang="en",
            kind="official",
            is_preferred=True,
        )
        cs.add(
            "name",
            entity_id=alliance,
            text="Partito Democratico – Italia Democratica e Progressista",
            lang="it",
            kind="native",
            is_preferred=False,
        )
        cs.add("citation", entity_id=alliance, source_id=sources[LIST])
        cs.add("alliance_member", alliance_id=alliance, party_id=PD, role="lead")
        for native, english in MEMBERS.items():
            party = cs.key("it.party", slug(native))
            if party in cs.minted:
                cs.add("party", id=party, kind="party", polity_id=italy)
                cs.add("name", entity_id=party, text=english, lang="en", kind="official", is_preferred=True)
                cs.add("name", entity_id=party, text=native, lang="it", kind="native", is_preferred=False)
            cs.add("alliance_member", alliance_id=alliance, party_id=party, role="list")
        return cs.write()
