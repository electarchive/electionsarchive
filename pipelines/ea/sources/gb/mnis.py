import json
import re
import time
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.mnis"
VERSION = "1"
BIOGRAPHY = "https://members-api.parliament.uk/api/Members/{}/Biography"
TITLE = "Members Names Information Service: member biographies"
HONORIFICS = re.compile(r"^(?:(?:Mr|Mrs|Ms|Miss|Mx|Dr|Sir|Dame|Rt Hon|Hon|Prof|Professor)\.? )+")


def members() -> list[tuple[UUID, str]]:
    with connect() as conn:
        return conn.execute(
            """select x.entity_id, x.value from external_id x join person p on p.id = x.entity_id
               where x.scheme = 'mnis' and not exists (select from entity_merge m where m.entity_id = x.entity_id)
               order by x.value::int"""
        ).fetchall()


def fetch() -> None:
    for _, number in members():
        url = BIOGRAPHY.format(number)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix=".json")
            time.sleep(1)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Each House of Commons member's preferred name set to the name they last sat under in the Commons, "
            "from Parliament's Members Names Information Service; other forms kept as alternative names",
            polity="GB",
        )
        source = cs.source("UK Parliament", TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="UK Parliament",
            title=TITLE,
            url="https://members-api.parliament.uk/",
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
        )
        preferred: dict[UUID, list[tuple[UUID, str, str, str, str]]] = {}
        for id, entity, text, kind, pipeline, lang in conn.execute(
            """select n.id, n.entity_id, n.text, n.kind, c.pipeline, n.lang from name n
               join changeset c on c.id = n.changeset_id
               join external_id x on x.entity_id = n.entity_id and x.scheme = 'mnis'
               where n.is_preferred and n.valid_from is null and n.valid_to is null"""
        ).fetchall():
            preferred.setdefault(entity, []).append((id, text, kind, pipeline, lang))
        renamed = curated = unnamed = 0
        samples: list[str] = []
        for person, number in members():
            try:
                artifact = raw.cached(SOURCE, BIOGRAPHY.format(number))
            except LookupError:
                unnamed += 1
                continue
            sittings = [
                membership
                for membership in json.loads(artifact.path.read_text(encoding="utf-8"))["value"]["houseMemberships"]
                if membership["house"] == 1 and membership["additionalInfo"]
            ]
            if not sittings:
                unnamed += 1
                continue
            cs.artifact(artifact, source)
            sat = HONORIFICS.sub("", max(sittings, key=lambda membership: membership["startDate"])["additionalInfo"])
            current = [row for row in preferred.get(person, []) if row[4] == "en"]
            if [row[1] for row in current] == [sat]:
                continue
            if any(row[3] == "identity" for row in current):
                curated += 1
                continue
            cs.add(
                "name",
                entity_id=person,
                text=sat,
                lang="en",
                kind="official",
                is_preferred=True,
                source_id=source,
            )
            for id, text, kind, _, lang in current:
                if text != sat:
                    cs.add("name", id=id, entity_id=person, text=text, lang=lang, kind=kind, is_preferred=False)
            renamed += 1
            if len(samples) < 25:
                samples.append(f"{' / '.join(row[1] for row in current)} -> {sat}")
        print(f"{renamed} renamed, {curated} left to the identity pipeline, {unnamed} with no Commons name")
        print("\n".join(samples))
        return cs.write()
