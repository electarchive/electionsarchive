import json
import re
import time
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.mnis"
VERSION = "2"
BIOGRAPHY = "https://members-api.parliament.uk/api/Members/{}/Biography"
TITLE = "Members Names Information Service: member biographies"
HONORIFICS = re.compile(r"^(?:(?:Mr|Mrs|Ms|Miss|Mx|Dr|Sir|Dame|Rt Hon|Hon|Prof|Professor)\.? )+")


PARTIES = {
    "Labour": ("wikidata", "Q9630"),
    "Conservative": ("wikidata", "Q9626"),
    "Liberal Democrat": ("gb.party", "liberal-democrats"),
    "Scottish National Party": ("gb.party", "scottish-national-party"),
    "Ulster Unionist Party": ("gb.party", "ulster-unionist-party"),
    "Democratic Unionist Party": ("gb.party", "democratic-unionist-party"),
    "Sinn Féin": ("gb.party", "sinn-fein"),
    "Plaid Cymru": ("gb.party", "plaid-cymru"),
    "Change UK - The Independent Group": ("gb.party", "the-independent-group-for-change"),
    "The Independent Group for Change": ("gb.party", "the-independent-group-for-change"),
    "Social Democratic & Labour Party": ("gb.party", "social-democratic-and-labour-party"),
    "Reform UK": ("gb.party", "reform-uk"),
    "Green Party": ("gb.party", "green-party"),
    "Alliance": ("gb.party", "alliance-party-of-northern-ireland"),
    "Vanguard Unionist Progressive Party": ("gb.party", "vanguard"),
    "UK Independence Party": ("gb.party", "uk-independence-party"),
    "Your Party": ("gb.party", "your-party"),
    "Anti H Block": ("gb.party", "anti-h-block"),
    "Unity": ("gb.party", "unity"),
    "Alba Party": ("gb.party", "alba-party"),
    "Workers Party of Britain": ("gb.party", "workers-party-of-britain"),
    "Respect Party": ("gb.party", "respect-party"),
    "Ulster Popular Unionist Party": ("gb.party", "ulster-popular-unionist"),
    "Ulster Progressive Unionist Party": ("gb.party", "ulster-progressive-unionist"),
    "United Ulster Unionist Party": ("gb.party", "united-ulster-unionist"),
    "Referendum": ("gb.party", "referendum"),
    "Republican Labour": ("gb.party", "republican-labour"),
    "The Reclaim Party": ("gb.party", "the-reclaim-party"),
    "Protestant Unionist": ("gb.party", "protestant-unionist"),
    "Restore Britain": ("gb.party", "restore-britain"),
    "Traditional Unionist Voice": ("gb.party", "traditional-unionist-voice"),
}
DATED = {
    "Liberal": ((date(1988, 3, 3), ("wikidata", "Q622441")), (date.max, ("gb.party", "liberal-party-1989"))),
    "Social Democratic Party": (
        (date(1990, 6, 3), ("gb.party", "social-democratic-party-1981")),
        (date.max, ("gb.party", "social-democratic-party-1990")),
    ),
}


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
            "from Parliament's Members Names Information Service; other forms kept as alternative names; and each member's "
            "dated Commons party affiliations as party memberships",
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
        renamed = curated = unnamed = memberships = 0
        skipped: Counter[str] = Counter()
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
            for affiliation in json.loads(artifact.path.read_text(encoding="utf-8"))["value"]["partyAffiliations"]:
                since = date.fromisoformat(affiliation["startDate"][:10]) if affiliation["startDate"] else None
                until = date.fromisoformat(affiliation["endDate"][:10]) if affiliation["endDate"] else None
                key = PARTIES.get(affiliation["name"]) or next(
                    (key for before, key in DATED.get(affiliation["name"], ()) if since is not None and since < before),
                    None,
                )
                if affiliation["house"] != 1 or since is None or key is None:
                    skipped[affiliation["name"]] += affiliation["house"] == 1
                    continue
                party = cs.key(*key)
                if party in cs.minted:
                    raise ValueError(f"no party {key}")
                cs.add(
                    "person_party_membership",
                    person_id=person,
                    party_id=party,
                    valid_from=since,
                    valid_from_precision="day",
                    valid_to=until,
                    valid_to_precision="day",
                    source_id=source,
                )
                memberships += 1
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
        print(f"{memberships} dated party memberships; not parties or not matched: {skipped.most_common()}")
        print("\n".join(samples))
        return cs.write()
