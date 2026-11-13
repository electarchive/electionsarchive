import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources.jp.mlit import LAW_PUBLISHER, LAW_TITLE, PREFECTURES
from ea.store import connect

SOURCE = "jp.diet"
VERSION = "1"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
HOUSES = (
    ("house-of-representatives", "lower_house", "衆議院", "House of Representatives", 465, date(2017, 10, 22)),
    ("house-of-councillors", "upper_house", "参議院", "House of Councillors", 248, date(2022, 7, 10)),
)
PARTIES = (
    ("Q232595", "Liberal Democratic Party"),
    ("Q99233271", "Constitutional Democratic Party of Japan"),
    ("Q41532587", "Constitutional Democratic Party of Japan"),
    ("Q200314", "Democratic Party of Japan"),
    ("Q23055252", "Democratic Party"),
    ("Q99527550", "Democratic Party for the People"),
    ("Q52729635", "Democratic Party for the People"),
    ("Q1061354", "Komeito"),
    ("Q641600", "Japanese Communist Party"),
    ("Q835109", "Social Democratic Party"),
    ("Q21499745", "Nippon Ishin no Kai"),
    ("Q1027055", "Japan Restoration Party"),
    ("Q18116909", "Japan Innovation Party"),
    ("Q64405573", "Reiwa Shinsengumi"),
    ("Q113009668", "Sanseito"),
    ("Q122639399", "Conservative Party of Japan"),
    ("Q134414127", "Team Mirai"),
    ("Q137787102", "Centrist Reform Alliance"),
    ("Q136820441", "NHK Party"),
    ("Q1355093", "Your Party"),
    ("Q241325", "People's New Party"),
    ("Q60658", "New Party Nippon"),
    ("Q1247614", "New Party Daichi"),
    ("Q194683", "Tomorrow Party of Japan"),
    ("Q1372123", "People's Life First"),
    ("Q2164308", "Liberal Party"),
    ("Q17488856", "Party for Japanese Kokoro"),
    ("Q7141081", "Party of Hope"),
    ("Q1571486", "Happiness Realization Party"),
    ("Q2094225", "New Renaissance Party"),
    ("Q1353524", "Sunrise Party of Japan"),
    ("Q865933", "Okinawa Social Mass Party"),
)

RENAMED = {
    "Q64405573": (
        ("Reiwa Shinsengumi", None, date(2026, 8, 6)),
        ("Party of Life", date(2026, 8, 6), None),
    ),
}


def fetch() -> None:
    for qid, _ in PARTIES:
        raw.fetch(SOURCE, ENTITY.format(qid), suffix=".json")


def day(qualifiers: dict[str, list[dict[str, Any]]], prop: str) -> date | None:
    values = [snak["datavalue"]["value"] for snak in qualifiers.get(prop, []) if "datavalue" in snak]
    return date.fromisoformat(values[0]["time"][1:11]) if values and values[0]["precision"] == 11 else None


def names(qid: str) -> list[tuple[str, date | None, date | None]]:
    entity = json.loads(raw.cached(SOURCE, ENTITY.format(qid)).path.read_bytes())["entities"][qid]
    dated = [
        (claim["mainsnak"]["datavalue"]["value"]["text"], day(qualifiers, "P580"), day(qualifiers, "P582"))
        for claim in entity["claims"].get("P1448", [])
        if claim["mainsnak"].get("datavalue", {}).get("value", {}).get("language") == "ja"
        and day(qualifiers := claim.get("qualifiers", {}), "P580") is not None
    ]
    return dated or [(entity["labels"]["ja"]["value"], None, None)]


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's Diet chambers, prefectural assemblies and governors, and the national parties "
            "that held Diet seats 2007-2026, linked to Wikidata",
            polity="JP",
        )
        law = cs.source(LAW_PUBLISHER, LAW_TITLE)
        wikidata = cs.source("Wikidata", "Japanese political parties")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Japanese political parties",
            url="https://www.wikidata.org/wiki/Q7278",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            attribution="Wikidata",
        )
        for qid, _ in PARTIES:
            cs.artifact(raw.cached(SOURCE, ENTITY.format(qid)), wikidata)

        def name(
            entity: UUID,
            text: str,
            lang: str,
            kind: str,
            source: UUID,
            start: date | None = None,
            end: date | None = None,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official",
                source_id=source,
                valid_from=start,
                valid_to=end,
            )

        japan = cs.polity("JP")
        for key, kind, native, english, seats, since in HOUSES:
            house = cs.key("jp.body", key)
            cs.add("body", id=house, polity_id=japan, kind=kind)
            name(house, english, "en", "official", law)
            name(house, native, "ja", "native", law)
            cs.add("body_size", body_id=house, seats=seats, valid_from=since)
        for number, (native, english) in enumerate(PREFECTURES, 1):
            code = f"{number:02d}"
            prefecture = cs.polity(f"JP-{code}")
            assembly = cs.key("jp.body", f"assembly-{code}")
            cs.add("body", id=assembly, polity_id=prefecture, kind="unicameral")
            name(
                assembly,
                "Tokyo Metropolitan Assembly" if code == "13" else f"{english} Prefectural Assembly",
                "en",
                "official",
                law,
            )
            name(assembly, f"{native}議会", "ja", "native", law)
            governor = cs.key("jp.office", f"governor-{code}")
            cs.add("office", id=governor, polity_id=prefecture, kind="governor")
            name(governor, f"Governor of {english}", "en", "official", law)
            name(governor, f"{native}知事", "ja", "native", law)
        for qid, english in PARTIES:
            party = cs.key("wikidata", qid)
            cs.add("party", id=party, kind="party", polity_id=japan)
            for text, start, end in names(qid):
                name(party, text, "ja", "official", wikidata, start, end)
            for text, start, end in RENAMED.get(qid, ((english, None, None),)):
                name(party, text, "en", "english", wikidata, start, end)
        return cs.write()
