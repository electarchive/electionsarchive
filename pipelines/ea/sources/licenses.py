from pathlib import Path

from ea.changeset import Changeset
from ea.sources.tr.ysk import LICENSE as TURKISH_OFFICIAL_ACTS
from ea.sources.us import elstats
from ea.store import connect

SOURCE = "licenses"
VERSION = "9"
PUBLIC_RECORD = "Public record"
UNLICENSED = "No license stated: facts published with attribution, raw files not redistributed"
NEWS = "All rights reserved: a news article cited for one fact, not redistributed"
# Publishers whose source rows were recorded with no license: government results are public records, Turkish court
# decisions are free to reproduce once published, news articles are cited for single facts, and sources stating no
# license keep their raw files private.
PUBLISHERS = {
    "Cork Returning Officer": PUBLIC_RECORD,
    "Dublin County Returning Officer": PUBLIC_RECORD,
    "Galway Returning Officer": PUBLIC_RECORD,
    "Anayasa Mahkemesi": TURKISH_OFFICIAL_ACTS,
    "Washington Secretary of State": PUBLIC_RECORD,
    "Wisconsin Elections Commission": PUBLIC_RECORD,
    "Minnesota Legislative Reference Library": PUBLIC_RECORD,
    "New York City Department of City Planning": f"{PUBLIC_RECORD}; free download with DCP's disclaimer of warranty",
    "Conseil constitutionnel": "Freely reproducible with the Conseil constitutionnel's site cited as the source, without "
    "altering the text (legal notice)",
    "Hürriyet": NEWS,
    "Memurlar.net": NEWS,
    "Yeni Şafak": NEWS,
    "Son Dakika": NEWS,
    "OpenElections": UNLICENSED,
    "California State University, Sacramento, Institute for Social Research": UNLICENSED,
    "Statewide Database, University of California, Berkeley": UNLICENSED,
    "Iowa Democratic Party": UNLICENSED,
    "IBGE": UNLICENSED,
    "Tapu ve Kadastro Genel Müdürlüğü": UNLICENSED,
    "Wikipedia": "CC BY-SA 4.0",
    "U.S. Census Bureau": "Public domain",
    "House of Commons Library": "Open Parliament Licence v3.0",
    "Tribunal Superior Eleitoral": "Creative Commons Attribution",
    "Tribunal Superior Eleitoral, Seção de Arquivo": "Creative Commons Attribution",
    "Yüksek Seçim Kurulu": TURKISH_OFFICIAL_ACTS,
    "Alberta King's Printer": UNLICENSED,
}
KINDS = {
    "official_body": PUBLIC_RECORD,
    "news_agency": NEWS,
    "academic": UNLICENSED,
    "aggregator": UNLICENSED,
    "community": UNLICENSED,
    "compendium": UNLICENSED,
}
OGL = "Open Government Licence v3.0"
OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
GLA = "No license stated: the Greater London Authority reserves copyright and requires permission for re-use; facts published with attribution, raw files not redistributed"
COUNCIL = "No license stated: a UK local authority or returning officer notice with no re-use terms; facts published with attribution, raw files not redistributed"
CAIN = "CAIN terms: single copies for private study or personal, non-commercial use; other use needs the CAIN Director's permission"
# UK publishers first recorded as "Public record". The Electoral Office for Northern Ireland and the Electoral
# Commission publish results under the Open Government Licence; the GLA and councils state no re-use terms.
REVIEWED = {
    "Electoral Office for Northern Ireland": OGL,
    "Electoral Commission": OGL,
    "Greater London Authority": GLA,
    "London Elects": GLA,
    "Greater London Returning Officer": GLA,
    "City of London Corporation": COUNCIL,
    "West of England Combined Authority": COUNCIL,
    "South Yorkshire Mayoral Combined Authority": COUNCIL,
    "West Midlands Combined Authority Returning Officer": COUNCIL,
    "West of England Combined Authority Returning Officer": COUNCIL,
    "The Electoral Management Board for Scotland": COUNCIL,
    "CAIN (Conflict Archive on the Internet), Ulster University": CAIN,
    "CAIN Web Service, Ulster University": CAIN,
    "CAIN, Ulster University": CAIN,
}
ATTRIBUTIONS = {
    "City of Chicago": "This site provides applications using data that has been modified for use from its original source, "
    "www.cityofchicago.org, the official website of the City of Chicago. The City of Chicago makes no claims as to the "
    "content, accuracy, timeliness, or completeness of any of the data provided at this site. The data provided at this "
    "site is subject to change at any time. It is understood that the data provided at this site is being used at one's "
    "own risk.",
}
RESTAMPS = {
    "https://nh.electionstats.com": {
        "kind": "aggregator",
        "publisher": elstats.TENANTS["NH"][1],
        "url": elstats.TENANTS["NH"][2],
        "license": elstats.HELD["NH"],
    },
}
COLUMNS = ("id", "kind", "publisher", "title", "url", "license", "license_url", "attribution", "notes")


def _reviewed(publisher: str) -> str | None:
    if publisher in PUBLISHERS:
        return None
    if "Returning Officer" in publisher:
        return REVIEWED.get(publisher, COUNCIL)
    return REVIEWED.get(publisher)


def fetch() -> None:
    pass


def parse() -> None:
    pass


def emit() -> Path | None:
    """Correct the license on source rows the import pipelines published without one, and store the attribution
    notice a publisher's terms require."""
    with connect() as conn:
        changed = []
        for values in conn.execute(
            "select id, kind, publisher, title, url, license, license_url, attribution, notes from source "
            "where license is null or (publisher = any(%s) and license like 'No license stated%%')"
            " or publisher = any(%s) or url = any(%s)"
            " or ((publisher = any(%s) or publisher like '%%Returning Officer%%')"
            " and (license = %s or license = %s))",
            [list(PUBLISHERS), list(ATTRIBUTIONS), list(RESTAMPS), list(REVIEWED), PUBLIC_RECORD, UNLICENSED],
        ).fetchall():
            row = dict(zip(COLUMNS, values, strict=True))
            reviewed = _reviewed(values[2]) if values[5] in (None, PUBLIC_RECORD, UNLICENSED) else None
            fixed = {
                **row,
                "license": reviewed or PUBLISHERS.get(values[2], values[5]) or KINDS[values[1]],
                "license_url": OGL_URL if reviewed == OGL else values[6],
                "attribution": ATTRIBUTIONS.get(values[2], values[7]),
                **RESTAMPS.get(values[4], {}),
            }
            if fixed != row:
                changed.append(fixed)
        if not changed:
            return None
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Record the license of sources published without one, the attribution their terms require, and "
            "the publisher and terms of sources first stamped with the wrong ones",
            polity=None,
            kind="correction",
        )
        for row in changed:
            cs.add("source", **row)
        return cs.write()
