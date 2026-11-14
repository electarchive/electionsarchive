from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "es.euskadi_parties"
VERSION = "1"
TITLE = "Wikidata items of the Basque party records"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
MERGES = {
    "278ba42b-2f87-5958-bd6c-dea9acb0f097": "7be1ca5e-ad02-564b-9c4a-fe5d5aa8c4d8",
    "2b0c5eae-956f-5042-b379-662de1582a08": "5f19ca45-7048-5f30-8496-f5a9f7150017",
    "9d4b092a-1c32-5adb-bb95-1d67502752cb": "7d009a3a-6ce5-5282-bb85-b6523f165b1d",
    "6b004874-c5f3-588c-88b1-3688e486e81c": "9d831c91-7bcc-569e-98f2-5a583fdc5c40",
    "f14a35de-5430-5d25-9ce1-5fd172d6c9d8": "f1a9df7f-8e0a-5903-b7ab-b6b41dffd89a",
    "33fb0928-8648-5626-8201-0b8262ed76e5": "664c54e4-5c00-5116-91e6-df4cf91d4dfd",
    "2cc578d5-533c-55f6-9556-42ae5d05a596": "12367f42-726f-58d8-a738-72e9f0d03b70",
    "bb8bd12b-69a4-52d3-b431-f7d54f52efe6": "f5525009-dc72-5b1a-a4e7-51516465b6bd",
    "896b24b3-373d-5245-ae5b-d414f8b1ea80": "c38acb0a-34ce-5fe8-86c9-151d9bf8db68",
    "9f80fa10-e610-5d28-bed0-330755c6d353": "a9f425ef-55d0-59b9-8e66-2f49f887e42e",
    "e481cfe5-f90a-5b7a-a471-fcfb9dd70536": "04cfe47b-82cd-5025-bc04-fe8c1c843965",
    "e4d3e479-6242-52a3-871d-dbc2f857dcec": "cd67ef58-0840-555b-b41c-283edbead7f5",
    "21b6671e-ff4b-5777-a863-bb3c58efd829": "86242e25-0667-5527-86b5-7e468bbf789c",
    "e408bf84-a690-57da-83b5-b4826ff60e39": "b9f879bc-1b0b-5a0b-bd97-42241a144dda",
    "fbfe9cb4-d2d2-5087-8cd5-4a4624ecdb07": "87e7bedf-44b0-56b4-afd6-c74643939144",
    "9b175df2-e77c-5e92-8db2-c55d9bbb5493": "2fb658c3-582f-5475-88fa-826e073a3b30",
    "e4d6282b-398b-500c-9905-49928763d408": "2fb658c3-582f-5475-88fa-826e073a3b30",
    "4f037c61-f180-5cc1-ac94-2c7e758b31c1": "951040c7-b16b-59c2-8e08-1f9e0521c75d",
    "db02ec42-f57a-5cd7-aa37-ed746133a940": "0a0a8c96-47bd-5346-b6c4-fc9d6ad45020",
    "ac9f65d1-a05b-528c-b71d-84247a959d2e": "16f914fb-949b-5472-acf3-5a82677e8676",
    "d2debf57-eb16-5f69-9fd3-11e6f24946c6": "16f914fb-949b-5472-acf3-5a82677e8676",
    "4a258502-c4ba-5505-a764-2cd52d25bbb7": "65512255-9d76-5e56-bf79-12dc23e156cf",
    "87338a79-eaff-5b8b-8ff5-1ddd0d091849": "fcde5b74-1a14-5664-9414-56befeece19d",
    "8f30163b-1c4e-5ebd-bff0-b33a055d4c21": "c1c792e0-6ef6-5f21-857b-b2629c8e8051",
    "4d942a99-ce5b-59fc-ac2f-db002b15023c": "5af5de49-a2f5-5bf7-8ad7-47d356c6a08b",
    "51b1bd4c-3e41-5663-9594-915d8803b941": "5af5de49-a2f5-5bf7-8ad7-47d356c6a08b",
    "53e810a8-747e-5bb4-91c5-bf8d76e2ac19": "5af5de49-a2f5-5bf7-8ad7-47d356c6a08b",
    "59071f68-77a2-54ed-98ed-793d79ced14e": "5af5de49-a2f5-5bf7-8ad7-47d356c6a08b",
    "e8e5aa59-190d-5aa5-87c7-f5cab7cd8333": "3e6ad63a-95c6-5b91-94b1-663219f0d9c5",
    "dba78a3d-4eb0-5d88-9afb-8adc9e9376dd": "c1495f6a-2925-57bf-8ee0-c12f4f758ccd",
    "4c7a3e9f-3572-507e-a5c1-b5d5b67b528c": "2e6c4a64-fa02-511f-9c60-5db8adeda133",
    "9beafc1f-fa4c-5719-b8a9-b803ae82c830": "65bc766c-31f7-50d9-ab02-0f3bd780fb0d",
    "9d8a3fb0-1dd6-5aea-979a-909d4a45551d": "ce690f39-48ab-505d-83a6-a7f3ae44c3cb",
    "1335c2ff-4a96-53e8-a441-03207ca3c55c": "4276e2b6-7808-5256-9a8b-580667f542c5",
    "7d34c8d7-6c69-58cc-83d2-5f9d4186a803": "933b9648-3d27-54cc-80a6-c585cd6ad66d",
    "ac0a0235-97f6-51d5-9a5f-0f6f82ba1c9d": "5fb31580-9727-5a45-8c42-3beef895e17b",
    "e8b541ff-d10b-568e-96e3-0e17fee5de19": "0c05b9ab-4879-5515-b371-d07329a8c84e",
    "3a423b21-fcb8-505a-86be-d58d2fab3515": "fe9e1c94-1986-5592-9d09-f38fea870b7e",
    "32c1a1f8-9099-5d98-aeff-d50d8a4c6fbb": "df8bc3cc-4f36-5229-bd33-3d30edb688cf",
    "f81243e5-1679-5d9e-9fc2-8ac04901639f": "993a417e-c711-53b5-82bd-c85511d1b0c9",
    "17a64899-a3ed-5c71-84e1-ce0a900636b8": "60dc3f65-a0be-50bb-a767-8c015f9bbd7c",
    "62b1cf0a-0979-5f94-8d09-c878ae3a7a49": "01488b6e-f6f1-5cde-b95b-19a34bf67915",
    "99f2c4b3-ce2a-5dcf-bb73-33443ff10088": "1e639bba-6343-5d95-b44f-2d215943afe6",
    "e1321323-833c-511c-b5d4-6b8f08830328": "d6c8493f-50ae-55b5-a14f-2ade50cb2173",
    "d1ba2e5b-a01d-55ce-b865-0f609cb0b317": "e6071246-843a-5130-a10f-ba85ad154adb",
    "cba02ecc-3261-50bc-ab55-e6bc9d5b90f2": "71d44faa-d901-50df-8456-8b88e1bdd342",
    "1d5a8d64-6f82-5c8b-8425-ef35cbf81da7": "1cb50e34-9b51-5877-a303-5ac2340da603",
    "82c5ded2-72e6-58da-8151-890f25cbf6c3": "4f3e8525-cd41-50b5-a923-805ddb7eb443",
    "587b1df1-3308-5154-acae-420ef2212e3d": "b4ee756b-94ca-556e-a20b-7ffef3157b9b",
    "3569095a-d6a5-5544-bbfc-6096d471d992": "81af0b4b-274f-5530-82e8-f081ee85ba44",
    "c29a3e82-4545-51bb-91a9-9ec682135fe3": "b08737f4-7e6f-59ab-9738-ed55d98f6e75",
    "8c314589-045c-5d56-8f16-b1820054007d": "44661a39-eb2c-5ff1-8807-4a75bee2a7cf",
}
ITEMS = {
    "1e639bba-6343-5d95-b44f-2d215943afe6": "Q1443823",
    "4276e2b6-7808-5256-9a8b-580667f542c5": "Q3320404",
    "44661a39-eb2c-5ff1-8807-4a75bee2a7cf": "Q26409131",
    "8c69fe3b-47ec-501c-be47-1e89816f312d": "Q1169744",
    "b4ee756b-94ca-556e-a20b-7ffef3157b9b": "Q11953969",
    "fff36f44-8f8a-554d-9ab9-f3757240d2fa": "Q3326847",
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
                "Basque Parliament party records merged into the Spanish party records that carry the same party's "
                "Congreso, Senado, and European elections (PNV, PSE-PSOE, PP, EH Bildu, HB, EA, EE, IU, UPyD, Vox, "
                "PACMA, and smaller parties), Elkarrekin Podemos's 2016 and 2020 lists merged, and Basque-only "
                "parties linked to their Wikidata items"
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
