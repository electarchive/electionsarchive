from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb import offices
from ea.store import connect

SOURCE = "gb.lineage"
VERSION = "3"
TITLE = "UK party lineage: follows, followed by, inception, and dissolution (P155, P156, P571, P576)"
RELATIONS = (
    ("Q622441", "merged_into", "Q9624", date(1988, 3, 3), "day"),
    ("Q40052", "merged_into", "Q9624", date(1988, 3, 3), "day"),
    ("Q3360801", "successor_of", "Q622441", date(1989, 1, 1), "year"),
    ("Q1754707", "merged_into", "Q9626", date(1912, 5, 1), "month"),
    ("Q1754707", "merged_into", "Q7886824", date(1912, 1, 1), "year"),
    ("Q1311653", "successor_of", "Q476840", date(1882, 1, 1), "year"),
)
URWIN = "Scottish Conservatism: A Party Organization in Transition"
VICTORIAN = (
    ("whig", "merged_into", "Q622441", date(1859, 6, 6), "day", "willis"),
    ("radicals", "merged_into", "Q622441", date(1859, 6, 6), "day", "willis"),
    ("peelite", "merged_into", "Q622441", date(1859, 6, 6), "day", "willis"),
    ("irish-national-federation", "split_from", "Q1311653", date(1891, 1, 1), "year", "redmond"),
    ("irish-national-league", "split_from", "Q1311653", date(1891, 1, 1), "year", "redmond"),
    ("irish-national-federation", "merged_into", "Q1311653", date(1900, 1, 1), "year", "redmond"),
    ("irish-national-league", "merged_into", "Q1311653", date(1900, 1, 1), "year", "redmond"),
)
HISTORIES = {
    "willis": (
        "Liberal Democrat History Group",
        "On this day, 6 June 1859: formation of the Liberal Party",
        "https://liberalhistory.org.uk/timeline-event/on-this-day-6-6-1859/",
        "academic",
        (
            "Whigs, Peelites, and Radicals met at Willis's Rooms on 6 June 1859, the date generally taken as the "
            "formation of the Liberal Party."
        ),
    ),
    "redmond": (
        "National Library of Ireland",
        "John Redmond and the Irish Parliamentary Party",
        "https://www.nli.ie/1916/exhibition/en/content/stagesetters/homerule/johnredmond/",
        "official_body",
        (
            "After the Irish Parliamentary Party split over Parnell's leadership, Redmond led the Parnellite wing from "
            "1891 and became leader of the reunited party in 1900."
        ),
    ),
    "ulster": (
        "History Ireland",
        "The 1885 and 1886 general elections in Ireland",
        "https://historyireland.com/the-1885-and-1886-general-elections-in-ireland/",
        "academic",
        "Counts the sixteen Ulster seats won in 1885 by the Irish Conservatives as the Conservative Party's.",
    ),
}
ITEMS = sorted({item for origin, _, target, _, _ in RELATIONS for item in (origin, target)} | {"Q9630", "Q21942488"})
SERVICE = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": f"""SELECT ?a ?p ?v ?precision WHERE {{ VALUES ?a {{ {" ".join(f"wd:{item}" for item in ITEMS)} }}
{{ VALUES ?p {{ wdt:P155 wdt:P156 }} ?a ?p ?v . }} UNION
{{ VALUES ?p {{ p:P571 p:P576 }} ?a ?p [ psv:P571|psv:P576 [ wikibase:timeValue ?v ; wikibase:timePrecision ?precision ] ] . }} }}""",
        "format": "json",
    }
)


def fetch() -> None:
    raw.fetch(SOURCE, SERVICE, suffix=".json")
    for *_, url, _, _ in HISTORIES.values():
        raw.fetch(SOURCE, url, suffix=".html")


def emit() -> Path:
    artifact = raw.cached(SOURCE, SERVICE)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="UK party mergers and successions, and the Labour Party's first name, from Wikidata; the Unionist "
            "Party (Scotland) as the Conservatives' Scottish branch 1912–1965, and the Ulster Unionists allied with the "
            "Conservatives at each general election to 1970, as the Commons Library counts them",
            polity="GB",
        )
        source = cs.source("Wikidata", TITLE)
        cs.add(
            "source",
            id=source,
            kind="wikidata",
            publisher="Wikidata",
            title=TITLE,
            url="https://www.wikidata.org/wiki/Q9624",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(artifact, source)
        parties = {}
        for item in ITEMS:
            if item == "Q21942488":
                continue
            parties[item] = cs.key("wikidata", item)
            if parties[item] in cs.minted:
                raise ValueError(f"no party holds {item}")
        for origin, kind, target, day, precision in RELATIONS:
            cs.add(
                "party_relation",
                from_party_id=parties[origin],
                to_party_id=parties[target],
                kind=kind,
                date=day,
                date_precision=precision,
                source_id=source,
            )
        cs.add(
            "name",
            entity_id=parties["Q9630"],
            text="Labour Representation Committee",
            lang="en",
            kind="historical",
            is_preferred=False,
            valid_from=date(1900, 2, 1),
            valid_from_precision="month",
            valid_to=date(1906, 2, 15),
            valid_to_precision="day",
            source_id=source,
        )
        urwin = cs.source("D. W. Urwin", URWIN)
        cs.add(
            "source",
            id=urwin,
            kind="academic",
            publisher="D. W. Urwin, Political Studies",
            title=URWIN,
            url="https://vlex.co.uk/vid/scottish-conservatism-party-organization-874217393",
            attribution="D. W. Urwin, Scottish Conservatism: A Party Organization in Transition, Political Studies.",
            notes="Records that the Scottish Unionist Association's annual conference approved in April 1965 the change of the party's name to Conservative and its reform on the English model.",
        )
        cs.add(
            "party_relation",
            from_party_id=parties["Q7886824"],
            to_party_id=parties["Q9626"],
            kind="merged_into",
            date=date(1965, 1, 1),
            date_precision="year",
            source_id=urwin,
        )
        library = cs.source("House of Commons Library", offices.LIBRARY_TITLE)
        cs.add(
            "source",
            id=library,
            kind="official_body",
            publisher="House of Commons Library",
            title=offices.LIBRARY_TITLE,
            url="https://commonslibrary.parliament.uk/research-briefings/cbp-7529/",
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="Its notes say Ulster Unionists took the Conservative whip at Westminster until 1972 and are listed as "
            "Conservatives for general elections up to 1970.",
        )
        cs.artifact(raw.cached(offices.SOURCE, offices.LIBRARY), library)
        cs.add(
            "party_affiliation",
            party_id=parties["Q7886824"],
            parent_party_id=parties["Q9626"],
            kind="regional_branch",
            valid_from=date(1912, 1, 1),
            valid_from_precision="year",
            valid_to=date(1965, 1, 1),
            valid_to_precision="year",
        )
        cs.add("citation", entity_id=parties["Q7886824"], source_id=library)
        cs.add("citation", entity_id=parties["Q7886824"], source_id=urwin)
        ulster = cs.key("gb.party", "ulster-unionist-party")
        if ulster in cs.minted:
            raise ValueError("no gb.party ulster-unionist-party")
        generals = conn.execute(
            """select e.id, e.date_from from election e join polity p on p.id = e.polity_id and p.iso_code = 'GB'
               where e.kind = 'general' and e.parent_id is null and e.date_from <= '1970-12-31'
                 and exists (select from contest c join contest_option o on o.contest_id = c.id
                             where c.election_id = e.id and o.party_id = %s)
               order by 2""",
            [ulster],
        ).fetchall()
        for election, day in generals:
            alliance = cs.key(f"{SOURCE}.alliance", f"{day}:conservative-ulster-unionist")
            cs.add("alliance", id=alliance, kind="electoral_alliance", election_id=election)
            cs.add(
                "name",
                entity_id=alliance,
                text="Conservatives and Ulster Unionists",
                lang="en",
                kind="official",
                is_preferred=True,
                valid_from_precision="day",
                valid_to_precision="day",
            )
            cs.add("alliance_member", alliance_id=alliance, party_id=parties["Q9626"], role=None)
            cs.add("alliance_member", alliance_id=alliance, party_id=ulster, role=None)
            cs.add("citation", entity_id=alliance, source_id=library)
        histories = {}
        for key, (publisher, title, url, kind, notes) in HISTORIES.items():
            histories[key] = cs.source(publisher, title)
            cs.add("source", id=histories[key], kind=kind, publisher=publisher, title=title, url=url, notes=notes)
            cs.artifact(raw.cached(SOURCE, url), histories[key])
        for origin, kind, target, day, precision, history in VICTORIAN:
            party = cs.key("gb.party", origin)
            if party in cs.minted:
                raise ValueError(f"no gb.party {origin}")
            cs.add(
                "party_relation",
                from_party_id=party,
                to_party_id=parties[target],
                kind=kind,
                date=day,
                date_precision=precision,
                source_id=histories[history],
            )
        irish = cs.key("gb.party", "irish-conservative")
        if irish in cs.minted:
            raise ValueError("no gb.party irish-conservative")
        cs.add(
            "party_affiliation",
            party_id=irish,
            parent_party_id=parties["Q9626"],
            kind="regional_branch",
            valid_from=date(1885, 1, 1),
            valid_from_precision="year",
            valid_to=date(1886, 1, 1),
            valid_to_precision="year",
        )
        cs.add("citation", entity_id=irish, source_id=histories["ulster"])
        print(
            f"{len(generals)} general elections with Ulster Unionist candidates to 1970:", [day for _, day in generals]
        )
        return cs.write()
