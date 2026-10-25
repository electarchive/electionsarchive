from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "ie.lineage"
VERSION = "1"
REGISTER = "https://data.oireachtas.ie/ie/oireachtas/members/partyRegister/2015/2015-12-10_register-of-political-parties-10-december-2015_en.pdf"
REGISTER_TITLE = "Register of Political Parties, 10 December 2015"
NEWS = "https://www.irishtimes.com/news/politics/aaa-and-pbp-join-to-form-new-left-of-labour-party-1.2383848"
NEWS_TITLE = "AAA and PBP join to form new 'left of Labour' party"
REGISTERED = date(2015, 10, 8)
ALLIANCE = UUID("a825c1b4-6379-5a19-a886-b916941eb40b")
MEMBERS = (
    UUID("d1e08678-3324-5ffc-bb5a-1c60ee7d29c6"),
    UUID("49727ad0-d733-51a0-8f71-7cddda9b531c"),
)


def fetch() -> None:
    raw.fetch(SOURCE, REGISTER, suffix=".pdf")
    raw.fetch(SOURCE, NEWS, suffix=".html")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Irish party lineage: People Before Profit and the Socialist Party in Anti-Austerity Alliance–People Before Profit",
            polity="IE",
        )
        register = cs.source("Houses of the Oireachtas", REGISTER_TITLE)
        cs.add(
            "source",
            id=register,
            kind="official_body",
            publisher="Houses of the Oireachtas",
            title=REGISTER_TITLE,
            url=REGISTER,
            license="PSI General Licence",
            notes="Lists Anti-Austerity Alliance–People Before Profit, the Socialist Party, and People Before Profit as registered parties.",
        )
        cs.artifact(raw.cached(SOURCE, REGISTER), register)
        news = cs.source("The Irish Times", NEWS_TITLE)
        cs.add("source", id=news, kind="news_agency", publisher="The Irish Times", title=NEWS_TITLE, url=NEWS)
        cs.artifact(raw.cached(SOURCE, NEWS), news)
        for member in MEMBERS:
            cs.add(
                "party_affiliation",
                party_id=member,
                parent_party_id=ALLIANCE,
                kind="federation",
                valid_from=REGISTERED,
                valid_from_precision="day",
            )
            cs.add("citation", entity_id=member, source_id=news)
            cs.add("citation", entity_id=member, source_id=register)
        return cs.write()
