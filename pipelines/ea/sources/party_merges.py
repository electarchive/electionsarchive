from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "party_merges"
VERSION = "4"
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
            merged = conn.execute(
                "select exists (select from entity_merge where entity_id = %s)", [entity]
            ).fetchone()
            if not merged[0]:
                cs.merge(UUID(entity), UUID(into))
        return cs.write()
