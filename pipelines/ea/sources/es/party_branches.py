from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "es.party_branches"
VERSION = "1"
TITLE = "Wikidata items of the Spanish parties' regional branches"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
PP = "87e7bedf-44b0-56b4-afd6-c74643939144"
PSOE = "2fb658c3-582f-5475-88fa-826e073a3b30"
VOX = "65512255-9d76-5e56-bf79-12dc23e156cf"
CS = "fcde5b74-1a14-5664-9414-56befeece19d"
PODEMOS = "88e4c18d-54f1-5be4-939a-3e2391a4ac00"
EQUO = "fe9e1c94-1986-5592-9d09-f38fea870b7e"
PSDEG = "4b6b9191-5cb2-52ae-bbfc-947e660d0cf2"
PSN = "4601fb64-6c33-581f-a94d-a26933d74cf7"
NAVARRA_SUMA = "ab215948-c82b-520d-b2ca-2fba7eeaf46a"
MERGES = {
    "67acfaff-c6cc-5777-ae92-763210a9d529": PP,
    "2679a38b-3a5c-52cb-a76a-c2ef4f4851f1": VOX,
    "9c522dcf-54e8-5377-8388-65109d0ec44d": CS,
    "baaf8b3b-bfab-51ab-a5bc-baa8efbe3bec": PODEMOS,
    "660ecd98-af47-5f5d-97df-b715dee67c46": "19b17910-5009-5f63-9f64-f4d1fc0c2646",
    "9cfd1024-2cdf-540f-85b6-5bc4191fa7ad": PP,
    "ec9c4167-5a1b-5a6d-b651-cb193ab42376": PSDEG,
    "7efa5301-d122-5f7d-ad52-912a93936e51": "361cc2cf-a243-56b2-84f9-3a9d5c93b4d9",
    "0925ab14-a138-5fe5-972a-2624ea8e30df": "12367f42-726f-58d8-a738-72e9f0d03b70",
    "b33d4108-282a-523e-ada2-69767ac33da7": PSN,
    "60be4d60-8f85-514e-9612-79d8e5ae53a9": PSN,
    "043fde12-f69c-5974-9326-8afb70d48666": "26fe7f5d-b9ef-5bac-8313-8d175c7b9cd4",
    "79a145ff-3380-56df-85d8-87521176849a": NAVARRA_SUMA,
    "10b37b15-f4d7-510d-99c8-c74d973f7159": PODEMOS,
    "c2598529-185b-544f-a4a8-de8d1f404f5d": PP,
    "02b6eaee-1ce2-5b91-abd3-9cbab0b9800a": VOX,
    "b71801c0-095b-50a3-97ea-f2720a4355aa": CS,
    "1fffa340-cf1c-5acd-b45b-265f4821907b": EQUO,
    "9e95c3c0-75b9-5f15-88f8-5d10b03db366": "16f914fb-949b-5472-acf3-5a82677e8676",
    "d6f2f8c8-70f8-5122-911f-94273853f798": "5af5de49-a2f5-5bf7-8ad7-47d356c6a08b",
    "b97ac3fd-1f33-50fb-89f0-a90fe93dfbee": PODEMOS,
    "71808374-44e3-5c13-9e19-374791229793": CS,
    "0177a6ef-30d2-5a4e-b260-6fdf0b983d41": EQUO,
}
BRANCHES = {PSDEG: ("Q3047694", PSOE), PSN: ("Q280906", PSOE)}
ITEMS = {PSN: "Q280906", NAVARRA_SUMA: "Q62060692"}


CITED = sorted({*ITEMS.values(), *(item for item, _ in BRANCHES.values())})


def fetch() -> None:
    for item in CITED:
        raw.fetch(SOURCE, ENTITY.format(item), suffix=".json")


def emit() -> Path:
    with connect() as conn:
        live = {
            str(id)
            for (id,) in conn.execute(
                """select p.id from party p where p.id = any(%s)
                   and not exists (select from entity_merge m where m.entity_id = p.id)""",
                [[UUID(id) for id in {*MERGES, *MERGES.values(), *BRANCHES, PSOE}]],
            ).fetchall()
        }
        if gone := {*MERGES, *MERGES.values(), *BRANCHES, PSOE} - live:
            raise ValueError(f"parties missing or merged away: {sorted(gone)}")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Regional party records in Aragon, Galicia, Navarre, the Basque Country, and La Rioja that are the "
                "Spanish party itself (PP, Vox, Ciudadanos, Podemos, Equo, BNG, UPN, Navarra Suma, UPyD, PACMA, and "
                "Izquierda Unida) merged into the Spanish record that holds its Wikidata item; the socialist "
                "federations of Galicia and Navarre recorded as regional branches of the PSOE"
            ),
            polity=None,
            kind="correction",
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for item in CITED:
            cs.artifact(raw.cached(SOURCE, ENTITY.format(item)), source)
        for entity, into in MERGES.items():
            cs.merge(UUID(entity), UUID(into))
        held = dict(
            conn.execute(
                "select value, entity_id::text from external_id where scheme = 'wikidata' and value = any(%s)",
                [list(ITEMS.values())],
            ).fetchall()
        )
        for party, item in ITEMS.items():
            if held.get(item, party) != party:
                raise ValueError(f"{item} is held by {held[item]}, not {party}")
            cs.add("external_id", entity_id=UUID(party), scheme="wikidata", value=item)
            cs.add("citation", entity_id=UUID(party), source_id=source, locator={"item": item})
        for party, (item, parent) in BRANCHES.items():
            cs.add("party_affiliation", party_id=UUID(party), parent_party_id=UUID(parent), kind="regional_branch")
            cs.add("citation", entity_id=UUID(party), source_id=source, locator={"item": item, "parent": "PSOE"})
        return cs.write()
