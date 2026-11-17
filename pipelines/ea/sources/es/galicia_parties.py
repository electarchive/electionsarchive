from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "es.galicia_parties"
VERSION = "1"
TITLE = "Wikidata items of the Galician party records"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
MERGES = {
    "e243fab1-2349-5c0e-ad5d-352b4e62bc95": "12367f42-726f-58d8-a738-72e9f0d03b70",
    "6b4ba371-44ec-5e81-b490-953723f84bf0": "ce827bd5-9fa5-537b-a895-e036cd774e76",
    "c2829d20-2ccf-5cae-afca-4efa77c14c22": "7cbf8cc1-f782-54e0-b9b7-4e40f6da6dbb",
    "e1501184-bb99-5618-ac07-e3a2663c959c": "3e6ad63a-95c6-5b91-94b1-663219f0d9c5",
    "d83bb431-e4ae-5375-9eda-9e766fb559eb": "9d831c91-7bcc-569e-98f2-5a583fdc5c40",
    "78b85b0b-4d70-5ac4-bfd6-d28c1560b104": "a6d716a6-48bf-569a-895c-14be3f065eba",
    "180353e1-b7ff-5183-8339-27104877e951": "234d17de-3a06-5667-a001-b92b79b47e90",
    "3d4bba04-0c38-59f7-a72c-28cb4f0faa77": "12367f42-726f-58d8-a738-72e9f0d03b70",
    "3edb1aa4-2732-5729-9cdd-ffb566623fd4": "760f5040-0c8f-5750-b84b-1f1f6e6ac3be",
    "d5d70153-b2da-51bf-bef2-730227c04c25": "d5a03ca4-840d-531c-a17a-7cf39fc25655",
    "247f08f9-e9d3-5277-9270-2b01e5c8cab9": "54c33a72-d472-5274-9fdc-449b8b6a46f9",
    "f7af8bf0-ee6f-5632-a07d-c8a645a780e1": "2586f74e-a3d4-5ec0-b660-c22db647f672",
    "c0c1df98-0188-570a-807d-e34b2c355f7f": "b13b9081-44ce-522c-a152-d22300e70973",
    "fc639684-8df8-5668-9f80-22fb41db2435": "dbeef002-7ad2-5589-89ee-0eb65d78cd0f",
    "6bbd5326-8097-585a-9ff4-3e4f998adb9c": "fb5a0f83-9384-5158-a007-1c7182c52d46",
    "abffb085-b6cf-5b8b-b385-bb128380a76f": "c794b73c-4607-56aa-b6ca-67935213498d",
    "1d327901-57c5-5dc6-a293-3ff0470f1a8d": "16f914fb-949b-5472-acf3-5a82677e8676",
    "518fcb8d-343a-5de2-bc2f-f5d2fb5ca47f": "bb07fa48-fabf-5519-8eda-0f0e93c902d3",
    "b57cddf5-d5b8-56a3-ac35-82ffc3a763a2": "33fb3aae-f7f4-575f-b5fa-b55aa8050333",
    "e1a10fc0-1d87-591b-83f5-25e71c22d175": "531ee4c1-19f7-5cf7-b9ef-e610dee7e673",
    "0029533e-1e1e-5dcd-b169-96c79fddbf9d": "ebf2377d-64a8-52a4-a478-47140cfe5160",
    "6ae2ab09-0fdd-5847-bfaa-4bb3fd5d26e4": "2589eeeb-23ac-557c-aebd-1bafd1cc755a",
    "0232d108-9c17-5dec-bae5-38d52a0b5feb": "01488b6e-f6f1-5cde-b95b-19a34bf67915",
    "74e648ac-3aa7-54f6-bc39-ac2a07815214": "08c392a4-70ce-5a08-9add-2234a43c2bd7",
    "c016c747-ae12-5d91-bd54-83e79a0d2a4f": "16e1f999-55fc-5bb2-9769-a09bf9fa4873",
    "a790a15c-ad19-5558-b5a3-1810d8eea86f": "0c2daf69-86d8-5456-87dd-9de6eab1f615",
    "94c34cb9-f9fa-5e0a-bf95-cf970578fa43": "4317f64f-8be8-5169-8ee4-025065239f89",
    "54708fb1-34e3-5561-801f-d127157fef79": "705d00be-c858-5e5c-b67e-da5c623a347d",
    "18c591f6-63e9-5142-b382-d6604846c273": "0c05b9ab-4879-5515-b371-d07329a8c84e",
    "650a36a3-684f-545c-92d7-f95a140d7a48": "f5fa5ec4-5276-5532-a781-9f2888fee542",
    "44c3fb23-9ce9-517e-a4f1-c2baec0706e2": "993a417e-c711-53b5-82bd-c85511d1b0c9",
    "3cb00866-15e8-5226-96e6-0de72461ac1b": "b5927840-32c1-5090-b56a-2b6ded8bc9de",
    "2cab3340-0c7e-5f7b-9aab-0624148f3600": "fcde5b74-1a14-5664-9414-56befeece19d",
    "becfe477-80c0-53d9-809f-d41f7d4be4b2": "b08737f4-7e6f-59ab-9738-ed55d98f6e75",
    "865cecdc-beaf-5957-92f1-7a7e015011ac": "40e2ba21-bf30-57ee-9bf4-c0ff6ca2e208",
    "91589c09-ee06-5a51-8001-36f4be51237e": "fe9e1c94-1986-5592-9d09-f38fea870b7e",
    "2e39c4ab-0e5f-54be-8aaa-d6951e904be8": "ceac728f-3fec-5fa0-99a7-821f0c65846d",
    "7668bb27-593c-5a6c-bdab-659842fe31d0": "342ad78f-2149-5635-a007-0042bd71253a",
    "322e8e49-0a16-54c1-a343-0b6c660cba5a": "f82f92f6-2e36-577b-987d-7f9ff6ac309e",
    "85e9a5ca-9d10-51a7-8bdf-d79d3f1ace23": "767173cc-e6a0-505f-99b5-56ee00ff5251",
    "94227cee-a337-5ca3-962c-66b4e3fbbb92": "65512255-9d76-5e56-bf79-12dc23e156cf",
    "f8b31931-26e7-5def-a88d-ea6cb88d5f19": "16e1f999-55fc-5bb2-9769-a09bf9fa4873",
}
ITEMS = {
    "f00054e5-0f3d-5176-86e8-fae676fca325": "Q12401587",
}


def fetch() -> None:
    for item in sorted(set(ITEMS.values())):
        raw.fetch(SOURCE, ENTITY.format(item), suffix=".json")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Galician Parliament party records merged into the Spanish party records that carry the same party's "
                "Congreso, European, and municipal elections (Vox, Ciudadanos, UPyD, Izquierda Unida, PACMA, CDS, "
                "Falange, Equo, and smaller parties), Galician records of one party under several years' names "
                "merged, and Unión Coruñesa linked to its Wikidata item"
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
        for entity, into in MERGES.items():
            cs.merge(UUID(entity), UUID(into))
        taken = {value for (value,) in conn.execute("select value from external_id where scheme = 'wikidata'")}
        for party, item in ITEMS.items():
            if item in taken:
                continue
            cs.artifact(raw.cached(SOURCE, ENTITY.format(item)), source)
            cs.add("external_id", entity_id=UUID(party), scheme="wikidata", value=item)
            cs.add("citation", entity_id=UUID(party), source_id=source, locator={"item": item})
        return cs.write()
