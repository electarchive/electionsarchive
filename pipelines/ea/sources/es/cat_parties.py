from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources import partyfacts
from ea.store import connect

SOURCE = "es.cat_parties"
VERSION = "2"
TITLE = "Wikidata items of the Catalan party records"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
MERGES = {
    "01a7c07f-735d-5e3f-b617-657588ba3170": "3e6ad63a-95c6-5b91-94b1-663219f0d9c5",
    "82994c11-63a2-5d41-9ac8-1501569b473c": "bb07fa48-fabf-5519-8eda-0f0e93c902d3",
    "55da0bb0-8bb4-5bf1-805b-1f263192a9f8": "9d831c91-7bcc-569e-98f2-5a583fdc5c40",
    "c6f98e46-dff9-52fe-99eb-ca39342756ce": "01488b6e-f6f1-5cde-b95b-19a34bf67915",
    "d7d79709-3ad7-5efa-a340-d775d1fd560f": "d5a03ca4-840d-531c-a17a-7cf39fc25655",
    "6f4896e6-c6c7-5163-af26-8b9a0439aa1d": "7be1ca5e-ad02-564b-9c4a-fe5d5aa8c4d8",
    "a1f430f3-43b9-5d21-ace5-890da317199c": "16f914fb-949b-5472-acf3-5a82677e8676",
    "075c8067-b44a-5e57-8096-f3e18d22acc1": "58fb2dcd-44a2-589d-b05e-796d16e6b77a",
    "c1163870-a956-55b8-bcb0-8e7f53086f2e": "88aab1f7-47a8-5286-9798-2dab23d3cb57",
    "b130ff71-5510-5402-93e8-d88259089091": "699cda2d-2d32-5af6-bae9-1f29b02e256f",
    "1ea4c440-51fc-5ee5-9935-21442e028026": "c1495f6a-2925-57bf-8ee0-c12f4f758ccd",
    "f4cf8a08-c18a-5c15-ab34-bd752e5e03ed": "951040c7-b16b-59c2-8e08-1f9e0521c75d",
    "b39f4caf-76d5-5ddd-b1f7-77779bc0c21d": "7f652166-b39e-5f24-934e-0233fd66ea80",
    "997ed7fb-e0b9-5d41-a620-6b58e4212cbb": "16e1f999-55fc-5bb2-9769-a09bf9fa4873",
    "b6eb45eb-a810-5346-bfc0-b230ba1346e1": "c988d537-737b-5d78-b7f9-d0ef91ab058d",
    "e0274ac3-5b1b-5156-bf3c-3216d2d89d80": "99f1ce14-bdee-52c9-9db3-2e3186b5a01d",
    "1369afbc-3d34-5761-88be-2a63e9e4c8d6": "7302d2cd-5bb1-50b0-8fb3-1a2c179fc01c",
    "0060ecc3-f226-53e9-825e-91fa1c31c870": "67933b77-60af-586c-a0fa-e84753d56ed1",
    "5475d35a-2566-5ae6-9680-567a09b3c15a": "903e23cf-93a5-54bf-b757-b6646e6f78fa",
    "d816bc93-1728-56d6-aeb4-764608998bf3": "5db93a2b-4783-5f90-b2b6-17ee9901d5c3",
    "1db481f3-eb2b-5fd1-826e-56279650edae": "10cb3e05-52d2-58f9-9723-560d8583356d",
    "313d76da-0f74-5622-91a6-763ec6ea8c70": "3cb7ae59-16df-5200-b284-9a4095b8f6e3",
    "d801efc5-0f83-56f3-b4d7-e0b97c74f610": "00b4cda4-04d0-54bd-8e63-22316ab98df6",
    "33090c8c-930a-59a6-ba6e-03b216bc47c8": "fe284457-21c5-5590-9490-fee0dfbe67f6",
    "cd36b040-8e2a-553f-9d1c-c57a0c86a67b": "ce793c03-0402-57ff-a4d5-2d66d5659bc4",
    "ed93e65b-b2ac-53da-87c4-a973faff65f4": "7954bbab-dba6-50b0-97d5-d27e53e5e1a9",
    "c8a9ea63-bc81-596d-83b1-0ca5230c3ef5": "480d8c14-9165-5c02-b14e-930ffbdd7038",
    "bbdc2d08-9660-5bf6-9d70-ee4de5338162": "cf7a3f4e-5879-5106-ab07-dc6bb7fbab01",
    "0785c238-15fa-5162-8161-176d47646ce7": "2aeb8679-5eaf-5180-8646-8f21e68c78e1",
    "830f64ef-666b-5341-9cbd-4d7b81fdd55e": "bd6cff1e-f906-564c-88e5-9e6a4f6c1be8",
    "85e63252-710c-5ac7-ac3a-5c801177ddb1": "88e6c491-fbd7-5b18-8b96-5cc36eb6b695",
    "6299ae9e-4790-5c07-b58e-b82416f6f8f2": "4ff7474e-f028-5f18-a7e4-7874559b3225",
    "ee3aca20-8a9f-5942-a833-7a708b02ab72": "1cb50e34-9b51-5877-a303-5ac2340da603",
    "4cc6b79f-4c9f-58cc-81ed-c9fd9a5e3000": "ac2ad921-41c5-5cab-b8cd-29767d56ad42",
    "8ce48d29-e6a1-5ee5-8bef-01060d484c3e": "0b6df654-e9fc-52bb-a000-83b3c74e2ba2",
    "dcac115a-743d-58e9-8f48-b27b437a93cd": "0cf3f9e7-6f0c-512d-81b8-a66b3a4ccb14",
    "d9782288-2752-565c-bc3d-b9cfb0c6e035": "f15217a9-9228-5025-8b7a-d9811a267e03",
    "f4f6caaf-4e17-5e2b-8e9d-317eb6433b0e": "94703bf8-b0c3-576d-b68a-e3fd090af255",
    "af765534-40f7-53eb-8aba-910dd348ca50": "cf1a5493-c7e1-53c3-a896-680e1f189b18",
    "0ddee10a-3331-5038-87a6-52cd31ab3e95": "7bb80787-c8e1-50db-8c85-ab6f181847e8",
    "828e3160-3a55-55fc-9e42-ddeb5e214f99": "cfb7109a-9271-50f8-9a62-bc96e199affa",
    "77015b6a-b1e8-5c0b-bbfb-356fdc88722f": "006db217-5c82-5558-8d5c-ccf47478c99d",
    "f7ed537f-579e-5679-ad42-937a7cdeba20": "2c4c4a14-e82c-5a29-a11b-513c78e4e55c",
    "01fc253d-9cc0-5775-a8e6-bd70e9038b72": "b8bf55b8-fc74-5a30-90b4-11199355e46d",
    "81cab7a6-23ec-5057-b07c-c2948e446793": "67dd7ed8-8fa0-5a84-91bc-5c522bd3af7e",
    "8c85b946-2011-5947-9ad4-16a84904ac68": "c47a35c0-0d74-5c29-8617-fb9cb49301bc",
    "ef827945-4771-505f-a623-264b3efe388e": "1c23442f-36a3-5a12-bc09-fe1fb0899beb",
    "d9f6846b-937b-5146-94ee-0488456cc877": "5f19ca45-7048-5f30-8496-f5a9f7150017",
    "79062a34-c3f2-512c-bc0c-243d888ac3ff": "117eaeb1-d54e-5b69-b29a-26d75bbe89e6",
    "92578b7e-87f9-5f2c-8c00-ded9cf0e295f": "8122c425-db49-5588-87b8-f26a2950ac90",
    "e2635e97-13da-51f6-92db-6d2bba741d92": "23b9cb10-308b-5590-9ec1-a36b694750de",
    "5110fcd5-6009-5fdb-b4a8-a8b607ef6dc1": "1aaea521-65e3-58bc-875d-60cb95952ec1",
    "044c55ec-59a7-5e21-aff9-068adb6426f6": "a2e60b18-7bee-501b-a15d-23cb8b80cebb",
    "78ae3ca9-5d91-5ef3-b212-7e1b388d0a4d": "69c81482-b1c7-59bd-8174-41836ae1db00",
    "1833937e-db9e-59e4-b914-9e5a582a0105": "51211633-c5b6-5859-9320-788be0c7db71",
    "45e9676e-7ccc-53bc-810e-0f82a245a736": "f4793729-d7e2-54d8-ad6c-005e908a59ed",
    "4dd4388e-e81f-55c0-9258-df0957a52625": "f4793729-d7e2-54d8-ad6c-005e908a59ed",
    "74b26422-0e37-5dac-84a8-5734cf8bd9e8": "f4793729-d7e2-54d8-ad6c-005e908a59ed",
    "9315268a-fb99-53b4-ac81-d853eac65f59": "f4793729-d7e2-54d8-ad6c-005e908a59ed",
    "58e3809d-c4ea-5721-9ba0-a41f85e34819": "aabe914a-a1d9-56fa-a364-9ed95b5a390e",
    "cbe2ad82-ae3e-5838-9a65-6a14ba49184b": "8403ef5f-fa92-5654-ab9a-54d75d742b86",
}
ITEMS = {
    "b987c276-8ce2-5a84-a3d9-e066ee56046b": "Q2055003",
    "a27d2df7-719c-5063-9e5f-34a615b95a48": "Q66305492",
    "298e9c77-0f43-5d35-8156-0900f66d0abb": "Q66305500",
    "374c4de3-5a96-57d3-b852-0f94e92c9544": "Q20687291",
    "e612ec01-0707-50b8-a9bd-4b9aafc43459": "Q43228047",
    "a8e8236e-69fe-592f-82a6-adce5388947e": "Q8343566",
    "0ae8c605-6f60-58ee-8182-aae4f36f3cce": "Q64699408",
    "56e2d5ca-953e-56d1-a28e-ffedaf958ccf": "Q20669461",
    "e4e5af3c-039b-5bc8-bc73-bc393c2bc2ee": "Q104760691",
    "a4bc77e2-f47c-58cd-9e5e-b5851045cfaf": "Q104760756",
    "24267a86-1eba-5400-88ed-be9a18e8fed5": "Q43305850",
    "2c1ea6eb-6c08-5bcc-a6f0-4a236bb0d069": "Q125207815",
    "67779714-7141-5ffe-899d-d915ec816228": "Q62601058",
    "cea66e58-3ad1-5d1c-998b-c1424e98a688": "Q42937287",
    "18c00e6f-5437-55bf-9458-001ac631dad5": "Q57897379",
    "34aa140b-a75d-5e21-b549-ae2179fdd46b": "Q66362711",
    "ee7c8ddb-e0f9-5864-8ff5-25919819f7a4": "Q104760919",
    "35813c32-a981-554e-9d14-9883d776df9e": "Q66909581",
    "fdee9a53-4260-5ab3-8f6a-5ae6cbbea36d": "Q66298902",
    "c923b10e-d7dd-5afb-9a7a-9769b606a83f": "Q4897404",
    "20850041-6691-58a1-84dc-5bce8fd0eefc": "Q58170790",
    "a602debf-5472-53f7-ab99-2c554279c5a3": "Q6156073",
    "f1806d2a-b5cc-5993-ac80-ac15cbe4cd3f": "Q5798106",
    "795d1380-319d-5db4-ac3d-47c87b70c72f": "Q3134856",
    "493e3bc2-da45-5f43-817e-58117f2feb73": "Q42937669",
    "f7af0f6d-ecdb-55fb-8bfe-cb2d1d956c4b": "Q125451773",
    "9c0a47f4-3803-5d15-a344-60267606a042": "Q9056307",
    "29a6ff23-56c8-5fcd-ba74-bd2616815f04": "Q66305471",
    "1311cc3e-ecf6-5a1a-bb93-aab51ca91ee9": "Q21088782",
    "38de5895-4df8-5a60-a0e8-62585c578fe9": "Q66292962",
    "3b701814-fd5e-51ec-bc9c-4575de8fd7c6": "Q2891994",
    "06024872-d294-5731-a424-371cb3d82491": "Q55472006",
    "d77f6637-ebd4-57d6-af9f-3bca239e5380": "Q66309170",
    "125e1f5f-f8bd-5f26-9393-a3b04a240602": "Q66256574",
    "5f4e1730-cb16-53d4-8de0-579e08d340d3": "Q66256452",
    "971a8645-9c45-591b-9121-1e56522791ff": "Q66305473",
    "5249cf0a-55b7-5aed-a2f9-f49fb0374935": "Q104760849",
    "6c89d9e8-5149-51f5-b724-0c5cf41b2c03": "Q3752776",
    "64e17c49-850d-5752-9752-895920d7b211": "Q105336162",
    "c18e94c6-500c-500b-b9e7-a30398be45e7": "Q43771165",
    "78b4c29b-8917-5af0-ab2e-31b7d9cbbf06": "Q66256343",
    "ffd2374c-5a67-5b68-afcb-9137c52136f9": "Q3070137",
    "a5bfc13c-5986-555d-acc3-ce8dca3a057f": "Q11919086",
    "263aab41-272b-5e15-9c4f-ecc59d810371": "Q86079716",
    "1d5e9140-262d-59e6-b4cb-be35111ce9e1": "Q3813845",
    "df74fd36-62f1-53cc-8477-429e2127b032": "Q9056224",
    "e6d7a187-d7e6-5457-8c14-7c733be31424": "Q11919794",
    "a5497ec3-076a-55f9-bb6d-be165bebdd57": "Q63759527",
    "c3560131-e74f-5a5d-8f80-6cf6ac9423a9": "Q64018011",
    "a986351e-29b5-5a9b-bc90-af45a4f12d36": "Q67505401",
    "b813c758-1406-5d60-82f4-7c80b75222e8": "Q56556337",
    "3dd4251e-b579-55ad-ba00-2616c71c6971": "Q3752759",
    "e9dcd352-7205-5746-a6f5-0e9a8623226d": "Q62596367",
    "5db93a2b-4783-5f90-b2b6-17ee9901d5c3": "Q8196661",
    "7bb80787-c8e1-50db-8c85-ab6f181847e8": "Q3392703",
    "cf7a3f4e-5879-5106-ab07-dc6bb7fbab01": "Q8195556",
    "c1495f6a-2925-57bf-8ee0-c12f4f758ccd": "Q3118726",
    "88e6c491-fbd7-5b18-8b96-5cc36eb6b695": "Q3366679",
    "ce793c03-0402-57ff-a4d5-2d66d5659bc4": "Q67155366",
    "903e23cf-93a5-54bf-b757-b6646e6f78fa": "Q11919085",
    "88aab1f7-47a8-5286-9798-2dab23d3cb57": "Q2713131",
    "1cb50e34-9b51-5877-a303-5ac2340da603": "Q66309157",
    "3cb7ae59-16df-5200-b284-9a4095b8f6e3": "Q66821255",
    "006db217-5c82-5558-8d5c-ccf47478c99d": "Q66293908",
    "d5a03ca4-840d-531c-a17a-7cf39fc25655": "Q11681764",
    "699cda2d-2d32-5af6-bae9-1f29b02e256f": "Q5154467",
    "c988d537-737b-5d78-b7f9-d0ef91ab058d": "Q3322477",
    "bd6cff1e-f906-564c-88e5-9e6a4f6c1be8": "Q9048433",
    "4ff7474e-f028-5f18-a7e4-7874559b3225": "Q8350060",
    "0b6df654-e9fc-52bb-a000-83b3c74e2ba2": "Q66309163",
}
PARTYFACTS = {
    "b987c276-8ce2-5a84-a3d9-e066ee56046b": "586",
    "51211633-c5b6-5859-9320-788be0c7db71": "7421",
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
                "Parliament of Catalonia party records merged into the records that carry the same party's "
                "Congress, Senate, European, municipal, or other regional elections (Partido Humanista, CDS, UPyD, "
                "Plataforma per Catalunya, Unió Democràtica, ERC-Catalunya Sí, Junts, the Initiative for Catalonia "
                "lineage, and smaller parties), Catalan parties and coalitions linked to their Wikidata items, and the "
                "Party Facts IDs of the PSC and Junts"
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
        merged = {entity for (entity,) in conn.execute("select entity_id::text from entity_merge")}
        for entity, into in MERGES.items():
            if entity not in merged:
                cs.merge(UUID(entity), UUID(into))
        held = dict(conn.execute("select value, entity_id::text from external_id where scheme = 'wikidata'").fetchall())
        for party, item in ITEMS.items():
            if held.get(item) == party:
                continue
            cs.artifact(raw.cached(SOURCE, ENTITY.format(item)), source)
            cs.add("external_id", entity_id=UUID(party), scheme="wikidata", value=item)
            cs.add("citation", entity_id=UUID(party), source_id=source, locator={"item": item})
        known = dict(
            conn.execute("select value, entity_id::text from external_id where scheme = 'partyfacts'").fetchall()
        )
        core = cs.source(partyfacts.PUBLISHER, partyfacts.TITLE)
        for party, value in PARTYFACTS.items():
            if known.get(value) == party:
                continue
            cs.artifact(raw.cached(partyfacts.SOURCE, partyfacts.CORE), core)
            cs.add("external_id", entity_id=UUID(party), scheme="partyfacts", value=value)
            cs.add("citation", entity_id=UUID(party), source_id=core, locator={"partyfacts_id": value})
        return cs.write()
