from pathlib import Path

from ea.changeset import Changeset
from ea.sources.eu.gisco import STATES
from ea.store import connect

SOURCE = "eu.members"
VERSION = "1"
NATIVES = {
    "AT": ("Q40", "de", "Österreich"),
    "BG": ("Q219", "bg", "България"),
    "CY": ("Q229", "el", "Κύπρος"),
    "CZ": ("Q213", "cs", "Česko"),
    "DK": ("Q35", "da", "Danmark"),
    "EE": ("Q191", "et", "Eesti"),
    "ES": ("Q29", "es", "España"),
    "FI": ("Q33", "fi", "Suomi"),
    "GR": ("Q41", "el", "Ελλάδα"),
    "HR": ("Q224", "hr", "Hrvatska"),
    "HU": ("Q28", "hu", "Magyarország"),
    "LT": ("Q37", "lt", "Lietuva"),
    "LU": ("Q32", "lb", "Lëtzebuerg"),
    "LV": ("Q211", "lv", "Latvija"),
    "MT": ("Q233", "mt", "Malta"),
    "NL": ("Q55", "nl", "Nederland"),
    "PT": ("Q45", "pt", "Portugal"),
    "RO": ("Q218", "ro", "România"),
    "SE": ("Q34", "sv", "Sverige"),
    "SI": ("Q215", "sl", "Slovenija"),
    "SK": ("Q214", "sk", "Slovensko"),
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The member states of the European Union missing from the polity tree, each under the Union from its accession",
            polity="EU",
        )
        union = cs.polity("EU")
        parented = {
            iso
            for (iso,) in conn.execute(
                "select p.iso_code from polity p join polity_parent pp on pp.polity_id = p.id where pp.parent_id = %s",
                [union],
            )
        }
        for iso, (_, accession, _, label) in STATES.items():
            if iso == "GB" or iso in parented:
                continue
            polity = cs.polity(iso)
            if polity in cs.minted:
                wikidata, lang, native = NATIVES[iso]
                cs.add("polity", id=polity, kind="sovereign_state", iso_code=iso)
                cs.add("name", entity_id=polity, text=label, lang="en", kind="official", is_preferred=True)
                cs.add("name", entity_id=polity, text=native, lang=lang, kind="native", is_preferred=False)
                cs.add("slug", path=iso.lower(), entity_id=polity)
                cs.add("external_id", entity_id=polity, scheme="wikidata", value=wikidata)
                cs.add("polity_territory", polity_id=polity, area_id=cs.area("eu.member_state", iso))
            cs.add("polity_parent", polity_id=polity, parent_id=union, valid_from=accession)
        return cs.write()
