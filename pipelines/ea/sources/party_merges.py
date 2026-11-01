from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "party_merges"
VERSION = "6"
MERGES = {
    "330bfd15-7970-5a47-9c1a-cb2b2632b5d7": "bc509349-9b70-5653-a994-69f2077ef882",
    "9fab4680-3011-5c3a-bd33-bdc6e96693aa": "8788ac83-2ebc-584d-9382-d46868efd147",
    "1339c457-75e2-5f94-8334-c7263ac467b0": "c8dd9530-2c49-5e62-9e64-1743c05c1846",
    "0af0d07a-e0e8-5f28-8101-659abf8d2b4c": "31ec1356-56a0-5039-bf9b-86578e9afd2b",
    "1b94ae81-7de4-5ab1-86db-a1d3c2195806": "01a13bb5-c6e1-7410-816e-3b2f89b4ed1c",
    "22154c43-64ca-51af-a07f-c90138387984": "01a13ba1-d3a6-7084-b7c4-4e5edc77d918",
    "055a3eaa-b970-5247-9961-89667691fefb": "f4d6b254-a5ef-562d-813e-ce413c2037c6",
    "67fda830-80c9-5975-a316-f611441d26c8": "ce2006d2-f0ae-5fc6-9279-3a6e924bd4a0",
    "ac8b37ef-f631-5d97-b065-59307c4e3402": "ce2006d2-f0ae-5fc6-9279-3a6e924bd4a0",
    "01a13b7e-b158-7755-a39c-0b4a8f4ada26": "e5a44fd1-55cf-51c8-b70c-f4c8c4d4a6ad",
    "14a3aef4-1f43-5cd1-904b-3c4a6206c502": "64e4d6a9-f3a2-5eda-bd8f-59803a21612e",
    "3f27c6e1-6b07-5510-8c74-45adb2ce7457": "a9f92f6d-9811-5505-a2a0-0d2fb1bc215a",
    "8db41a1d-44a9-5ed7-b11b-e58fbb23bbfb": "01a14094-d257-77f4-bbc1-a316db3bfbcf",
    "ea04ea55-3200-5f6f-b769-e55946be80b2": "01a13bd3-6475-7344-8c51-dd4e83da5222",
    "a825c1b4-6379-5a19-a886-b916941eb40b": "01a13bd3-6475-7344-8c51-dd4e83da5222",
    "33df97c3-a596-5f59-9227-566ea74ed0b4": "2dfc0b3d-4e23-5e14-be27-8ef91f43ec0e",
    "71dcaef2-5c40-5330-bcff-4a189d59b390": "9ff51d01-f13d-5af8-a876-a27595afa4cf",
    "307180d2-65f3-5d40-bca4-5b0b89a563f7": "fcb086d6-ef40-5c4e-a62e-033854b073b0",
    "44181406-b19c-5f07-be95-cb0f9ac9f927": "17b3b0b1-cad3-5b22-b701-5f305e071e1a",
    "9742278a-80f2-5b3e-bccb-85b01ccc04eb": "2bca7926-fdd3-5e9a-889a-531b32e96ca9",
    "64d503ff-6822-5568-8d37-06c86211e567": "046c7191-aaf2-55a8-960c-4a8e33428415",
    "e60c72cd-0457-5fdb-a8b7-6c8d5f51c8fe": "f146df91-9888-5470-b138-7d334cbe7a44",
    "a496e345-32ca-534e-9066-e93761f2ef12": "8fec2942-7902-5d0f-a36e-e458ca59a8bd",
    "43ff1f53-68cb-5197-b6bd-16d8cd8fabdb": "33660a26-d195-50a2-a505-f90a9daecb58",
    "3beb1778-439f-5a65-9379-7b954d034e4d": "5e5b8165-c3f0-5100-b4f4-f9adf152939c",
    "173e73c7-d409-5766-b02e-2afa20f9d131": "5e5b8165-c3f0-5100-b4f4-f9adf152939c",
    "bfcd31ea-ea34-57fd-94f3-7bf76181fc9b": "6b4ce92d-96d9-5ed5-8ffd-5afcbdbc1e2b",
    "90245201-a954-50a9-b791-de21681cdc31": "e89ca231-79ad-5b9a-b319-0f9579491659",
    "16ed9ee8-671c-5fb0-bbc3-8c6effc6bc66": "f531cf55-0cff-5e42-b0b4-5f366ff4b5b6",
    "decadb53-e730-5947-928d-f2cbffccc144": "933c685f-6756-5684-bcce-171ac6c0c8a9",
    "9e9cf478-d831-50fb-8604-1dcbe00ef89e": "f5fe8cbc-050a-5762-937e-4129dc87e975",
    "47c81367-8e80-5a27-9440-5637c5b65d96": "fcb086d6-ef40-5c4e-a62e-033854b073b0",
    "09809440-9f17-5c43-883f-d92c46f33880": "93f3380f-2111-5f8d-9f44-cfc4181c16df",
    "ebc05e4f-d994-5697-a1b9-66826bc17f4c": "025b0880-7cf9-50e6-adaf-6acc17c79547",
    "f1af9cb3-8302-5072-83e4-cfe42eeb38d9": "025b0880-7cf9-50e6-adaf-6acc17c79547",
    "9e642475-115e-5942-91d9-bf7267ffbb5f": "01a13ba1-d3a6-7084-b7c4-4e5edc77d918",
    "b3b0a83f-55da-5600-a49c-c96af2f37dba": "01a13ba1-d3a6-7084-b7c4-4e6f52f34575",
    "3a3f4d1a-b29e-56d5-a13d-28d8cbb573ff": "01a13ba1-d3a6-7084-b7c4-4e6f52f34575",
    "01a14094-d257-77f4-bbc1-a318134dab58": "01a14094-d257-77f4-bbc1-a3191ff76419",
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "One party recorded twice by two pipelines, or twice by one, merged into the record that carries "
                "its other elections (PCF, PS, RPR, UMP, and the 2009 Majorité présidentielle list in France; NCD and the Südtiroler Volkspartei's 1948 and 1953 Senate lists, filed under their Edelweiss symbol, in Italy; Volt in Germany; the Partei der Arbeit in Switzerland; Podemos in Brazil)"
            ),
            polity=None,
            kind="correction",
        )
        for entity, into in MERGES.items():
            merged = conn.execute("select exists (select from entity_merge where entity_id = %s)", [entity]).fetchone()
            if not merged[0]:
                cs.merge(UUID(entity), UUID(into))
        return cs.write()
