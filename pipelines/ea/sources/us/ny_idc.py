import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ny_idc"
VERSION = "1"
TITLE = "Independent Democratic Conference"
PAGE = "https://en.wikipedia.org/wiki/{}"
WIKITEXT = "https://en.wikipedia.org/w/index.php?title={}&action=raw"
LICENSE = "Creative Commons Attribution-ShareAlike 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
SENATE = "416e1f55-c4ab-5bcb-850f-27affabd3ec4"
FOUNDED = date(2011, 1, 5)
COALITION = date(2012, 12, 4)
DISSOLVED = date(2018, 4, 16)
MEMBERS = [
    ("Klein", 34, 2012, FOUNDED, "day", DISSOLVED),
    ("Valesky", 53, 2012, FOUNDED, "day", DISSOLVED),
    ("Carlucci", 38, 2012, FOUNDED, "day", DISSOLVED),
    ("Savino", 23, 2012, FOUNDED, "day", DISSOLVED),
    ("Smith", 14, 2012, date(2012, 12, 1), "month", date(2013, 4, 14)),
    ("Avella", 11, 2012, date(2014, 2, 26), "day", DISSOLVED),
    ("Peralta", 13, 2016, date(2017, 1, 1), "month", DISSOLVED),
    ("Hamilton", 20, 2016, date(2017, 1, 1), "year", DISSOLVED),
    ("Alcantara", 31, 2016, date(2017, 1, 1), "year", DISSOLVED),
]


def fetch() -> None:
    raw.fetch(SOURCE, WIKITEXT.format(quote(TITLE.replace(" ", "_"))), suffix=".wiki")


def emit() -> Path:
    artifact = raw.cached(SOURCE, WIKITEXT.format(quote(TITLE.replace(" ", "_"))))
    text = artifact.path.read_text(encoding="utf-8")
    for family, *_ in MEMBERS:
        if not re.search(rf"\[\[[^\]|]*{family}[^\]]*\]\][^\n]*\(\d{{4}}[–-]\d{{4}}\)", text):
            raise ValueError(f"{family} is not in the article's membership list")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's Independent Democratic Conference, 2011–2018: the caucus, its members, and its coalition with the Senate Republicans",
            polity="US-NY",
            kind="edit",
        )
        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url=PAGE.format(quote(TITLE.replace(" ", "_"))),
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        cs.artifact(artifact, source)
        known = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.party' and value in ('democratic-party', 'republican-party')"
            ).fetchall()
        )
        idc = cs.key("us.party", "NY:independent-democratic-conference")
        if idc in cs.minted:
            cs.add("party", id=idc, kind="caucus", polity_id=cs.polity("US-NY"))
            cs.add("external_id", entity_id=idc, scheme="wikidata", value="Q29092981")
        for text, kind in ((TITLE, "official"), ("IDC", "abbreviation")):
            cs.add(
                "name",
                entity_id=idc,
                text=text,
                lang="en",
                kind=kind,
                is_preferred=kind == "official",
                valid_from=FOUNDED,
                valid_from_precision="day",
                valid_to=DISSOLVED,
                valid_to_precision="day",
                source_id=source,
            )
        cs.add(
            "party_affiliation",
            party_id=idc,
            parent_party_id=known["republican-party"],
            kind="coalition",
            valid_from=COALITION,
            valid_from_precision="day",
            valid_to=DISSOLVED,
            valid_to_precision="day",
        )
        for kind, day in (("split_from", FOUNDED), ("merged_into", DISSOLVED)):
            cs.add(
                "party_relation",
                from_party_id=idc,
                to_party_id=known["democratic-party"],
                kind=kind,
                date=day,
                date_precision="day",
                source_id=source,
            )
        for family, district, year, since, precision, until in MEMBERS:
            people = [
                person
                for (person,) in conn.execute(
                    """select distinct cd.person_id
                       from contest ct join election e on e.id = ct.election_id
                       join result_set r on r.contest_id = ct.id and r.is_default
                       join outcome o on o.result_set_id = r.id and o.kind = 'elected'
                       join candidacy cd on cd.option_id = o.option_id and cd.role = 'head'
                       join name n on n.entity_id = cd.person_id and n.text ilike %s
                       where ct.body_id = %s and e.kind <> 'primary' and extract(year from e.date_from) = %s
                         and exists (select 1 from name c where c.entity_id = ct.id and c.text ~ %s)""",
                    [f"%{family}%", SENATE, year, rf"District {district}$"],
                ).fetchall()
            ]
            if len(people) != 1:
                raise ValueError(f"{family}: {len(people)} winners of District {district} in {year}")
            cs.add(
                "person_party_membership",
                person_id=people[0],
                party_id=idc,
                valid_from=since,
                valid_from_precision=precision,
                valid_to=until,
                valid_to_precision="day",
                source_id=source,
            )
        return cs.write()
