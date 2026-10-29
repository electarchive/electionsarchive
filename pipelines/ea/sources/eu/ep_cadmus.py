import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources import parlgov
from ea.sources.gb import umit
from ea.store import DATA, connect

SOURCE = "eu.ep_cadmus"
VERSION = "7"
PARSED = DATA / "parsed" / SOURCE
URL = "https://cadmus.eui.eu/bitstreams/9a5261b3-8dea-5650-988a-fac4460a8f3c/download"
DEMON = "https://web.archive.org/web/20200201142936id_/http://www.election.demon.co.uk:80/eplon.html"
REPLACED = {
    (1984, "London South West"): (
        {"Conservative Party": 70490, "Labour Party": 63623, "Alliance Party": 32268, "Others": 3066},
        169447,
        (
            "Cadmus gives this seat the result of the London South West by-election of 20 September 1979 (Conservative "
            "41,096, Labour 32,632, Liberal 23,842, others 2,135). The votes shown are the 1984 result as Election Demon "
            "prints it: Conservative 70,490, Labour 63,623, Liberal (SDP–Liberal Alliance) 32,268, and Ecology 3,066, a "
            "turnout of 33.9 percent of 499,272 electors."
        ),
    ),
}
BY_ELECTION = (
    date(1979, 9, 20),
    513347,
    99705,
    (
        (
            "Shelagh Roberts",
            "Miss S.M. Roberts",
            ("gb.umit.person", "wls-caernarvon:roberts-shelagh-marjorie"),
            "conservative",
            41096,
        ),
        ("Tony Hart", "A.B. Hart", None, "labour", 32632),
        ("Christopher Mayhew", "C.P. Mayhew", ("wikidata", "Q5112833"), "liberal", 23842),
        (
            "Oliver Smedley",
            "W.O. Smedley",
            ("gb.umit.person", "eng-saffron-walden:smedley-william-oliver"),
            "anti-common-market-and-free-trade-party",
            1830,
        ),
        ("D. Hussey", "D. Hussey", None, None, 305),
    ),
)
BY_ELECTION_NOTES = {
    "D. Hussey": (
        "Election Demon labels Hussey an independent democrat: he stood as the candidate of the \u201cUnited "
        "Democratic Party\u201d, a name used by a mid-1970s party he appears to have had no connection to."
    ),
}
WINDOWS = {
    1979: (date(1979, 6, 7), date(1979, 6, 10)),
    1984: (date(1984, 6, 14), date(1984, 6, 17)),
    1989: (date(1989, 6, 15), date(1989, 6, 18)),
    1994: (date(1994, 6, 9), date(1994, 6, 12)),
    1999: (date(1999, 6, 10), date(1999, 6, 13)),
    2004: (date(2004, 6, 10), date(2004, 6, 13)),
    2009: (date(2009, 6, 4), date(2009, 6, 7)),
    2014: (date(2014, 5, 22), date(2014, 5, 25)),
    2019: (date(2019, 5, 23), date(2019, 5, 26)),
}
GENERALS = [*(window[0] for window in WINDOWS.values()), date(2024, 6, 6)]
GROUPS = {
    **dict.fromkeys(("PPE", "EPP-ED", "EPP"), "PPE"),
    **dict.fromkeys(("S", "PSE", "S&D"), "S&D"),
    **dict.fromkeys(("L", "LDR", "ELDR", "ALDE", "Renew Europe"), "Renew"),
    **dict.fromkeys(("V", "Verts/ALE", "VERTS/Ale", "Greens/EFA", "G/EFA"), "Verts/ALE"),
    **dict.fromkeys(("GUE/NGL", "GUE/NFL"), "The Left"),
    **dict.fromkeys(("Non-Inscrits", "non-inscrits", "Non inscrits"), "NI"),
    **dict.fromkeys(("EFD", "EFDD"), "EFD"),
    "CG": "LU",
    **{
        label: label
        for label in ("ID", "ECR", "ED", "COM", "CDI", "DEP", "RDE", "DR", "ARC", "FE", "EDN", "ARE", "UEN", "TDI")
    },
    "EDD": "EDD",
    "IND/DEM": "IND/DEM",
}
GROUP_NAMES = [
    ("PPE", 1979, 1999, "Group of the European People's Party (Christian-Democratic Group)", "EPP"),
    ("PPE", 1999, 2009, "Group of the European People's Party (Christian Democrats) and European Democrats", "EPP-ED"),
    ("PPE", 2009, 2019, "Group of the European People's Party (Christian Democrats)", "EPP"),
    ("S&D", 1979, 1994, "Socialist Group", "SOC"),
    ("S&D", 1994, 2009, "Group of the Party of European Socialists", "PES"),
    (
        "S&D",
        2009,
        2019,
        "Group of the Progressive Alliance of Socialists and Democrats in the European Parliament",
        "S&D",
    ),
    ("Renew", 1979, 1989, "Liberal and Democratic Group", "LD"),
    ("Renew", 1989, 1994, "Liberal and Democratic Reformist Group", "LDR"),
    ("Renew", 1994, 2004, "Group of the European Liberal, Democrat and Reform Party", "ELDR"),
    ("Renew", 2004, 2019, "Group of the Alliance of Liberals and Democrats for Europe", "ALDE"),
    ("Verts/ALE", 1989, 1999, "The Green Group in the European Parliament", "Greens"),
    ("Verts/ALE", 1999, 2019, "Group of the Greens/European Free Alliance", "Greens/EFA"),
    ("The Left", 1994, 2020, "Confederal Group of the European United Left/Nordic Green Left", "GUE/NGL"),
    ("NI", 1979, 2019, "Non-attached Members", "NI"),
    ("EUL", 1989, 1994, "Group for the European United Left", "EUL"),
    ("LU", 1989, 1994, "Left Unity", "LU"),
    ("ECR", 2009, None, "European Conservatives and Reformists Group", "ECR"),
    ("ED", 1979, None, "European Democratic Group", "ED"),
    ("COM", 1979, None, "Communist and Allies Group", "COM"),
    ("CDI", 1979, None, "Group for the Technical Coordination and Defence of Independent Groups and Members", "CDI"),
    ("DEP", 1979, None, "Group of European Progressive Democrats", "EPD"),
    ("RDE", 1984, None, "Group of the European Democratic Alliance", "EDA"),
    ("DR", 1984, None, "Group of the European Right", "ER"),
    ("ARC", 1984, None, "Rainbow Group", "RBW"),
    ("FE", 1994, None, "Forza Europa Group", "FE"),
    ("EDN", 1994, None, "Europe of Nations Group", "EN"),
    ("ARE", 1994, None, "Group of the European Radical Alliance", "ERA"),
    ("UEN", 1999, None, "Union for Europe of the Nations Group", "UEN"),
    ("TDI", 1999, None, "Technical Group of Independent Members", "TGI"),
    ("EDD", 1999, None, "Group for a Europe of Democracies and Diversities", "EDD"),
    ("IND/DEM", 2004, None, "Independence/Democracy Group", "IND/DEM"),
    ("EFD", 2009, 2014, "Europe of Freedom and Democracy Group", "EFD"),
    ("EFD", 2014, None, "Europe of Freedom and Direct Democracy Group", "EFDD"),
]
ISO = {
    "Austria": "AT",
    "Belgium": "BE",
    "Bulgaria": "BG",
    "Croatia": "HR",
    "Cyprus": "CY",
    "Czech Republic": "CZ",
    "Denmark": "DK",
    "Estonia": "EE",
    "Finland": "FI",
    "France": "FR",
    "Germany": "DE",
    "Greece": "GR",
    "Hungary": "HU",
    "Ireland": "IE",
    "Italy": "IT",
    "Latvia": "LV",
    "Lithuania": "LT",
    "Luxembourg": "LU",
    "Malta": "MT",
    "Netherlands": "NL",
    "Poland": "PL",
    "Portugal": "PT",
    "Romania": "RO",
    "Slovakia": "SK",
    "Slovenia": "SI",
    "Spain": "ES",
    "Sweden": "SE",
    "United Kingdom": "GB",
}
CONSTITUENCIES = {
    ("FR", "Center"): "massif-central-centre",
    ("FR", "East"): "est",
    ("FR", "North-west"): "nord-ouest",
    ("FR", "Outre-Mer"): "outre-mer",
    ("FR", "South-East"): "sud-est",
    ("FR", "South-West"): "sud-ouest",
    ("FR", "West"): "ouest",
    ("FR", "Île-de-France"): "ile-de-france",
    ("IE", "Connaught Ulster"): "connacht-ulster",
    ("IE", "Dublin"): "dublin",
    ("IE", "East"): "east",
    ("IE", "Leinster"): "leinster",
    ("IE", "Midlands-North-West"): "midlands-north-west",
    ("IE", "Munster"): "munster",
    ("IE", "North-West"): "north-west",
    ("IE", "South"): "south",
    ("IT", "Central"): "centrale",
    ("IT", "Islands"): "insulare",
    ("IT", "North-East"): "nord-orientale",
    ("IT", "North-east"): "nord-orientale",
    ("IT", "North-west"): "nord-occidentale",
    ("IT", "Southern"): "meridionale",
    ("GB", "East Midlands Region"): "east-midlands",
    ("GB", "Eastern Region"): "eastern",
    ("GB", "London Region"): "london",
    ("GB", "North East Region"): "north-east",
    ("GB", "North West Region"): "north-west",
    ("GB", "Northern Ireland"): "northern-ireland",
    ("GB", "Scotland"): "scotland",
    ("GB", "South East Region"): "south-east",
    ("GB", "South West Region"): "south-west",
    ("GB", "Wales"): "wales",
    ("GB", "West Midlands Region"): "west-midlands",
    ("GB", "Yorkshire and the Humber"): "yorkshire-and-the-humber",
}
PARLGOV_ISO = {
    "AUT": "AT",
    "BEL": "BE",
    "BGR": "BG",
    "HRV": "HR",
    "CYP": "CY",
    "CZE": "CZ",
    "DNK": "DK",
    "EST": "EE",
    "FIN": "FI",
    "FRA": "FR",
    "DEU": "DE",
    "GRC": "GR",
    "HUN": "HU",
    "IRL": "IE",
    "ITA": "IT",
    "LVA": "LV",
    "LTU": "LT",
    "LUX": "LU",
    "MLT": "MT",
    "NLD": "NL",
    "POL": "PL",
    "PRT": "PT",
    "ROU": "RO",
    "SVK": "SK",
    "SVN": "SI",
    "ESP": "ES",
    "SWE": "SE",
}
ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
NAMED_ITEMS = ["Q23731823", "Q205150", "Q541679", "Q1428001", "Q4732477"]
SURVIVORS = {
    "IT:Democratici di Sinistra": ("wikidata", "Q541679"),
    "IT:Partito dei Comunisti Italiani": ("wikidata", "Q1428001"),
    "IT:Lega": ("wikidata", "Q85776185"),
    "FR:Parti Socialiste": ("wikidata", "Q170972"),
    "FR:Parti socialiste": ("wikidata", "Q170972"),
    "FR:Parti Communiste Français": ("wikidata", "Q192821"),
    "FR:Parti communiste français": ("wikidata", "Q192821"),
    "FR:Rassemblement pour la République": ("wikidata", "Q1052584"),
    "FR:Alliance des Outre-mers": ("wikidata", "Q4732477"),
    "IE:Sinn Féin": ("ie.party", "SF"),
}
WRONG_NAMES = {
    "IT:Partito dei Comunisti Italiani": {"Italian Communist Party", "PCI"},
    "IE:Sinn Féin": {"[We] Ourselves"},
}
NO_PARTY = {"IE:Independents"}
DATED_NAMES = [
    ("Q205150", "Front national", "fr", date(1972, 10, 5), date(2018, 6, 1)),
    ("Q205150", "National Front", "en", date(1972, 10, 5), date(2018, 6, 1)),
    ("Q205150", "Rassemblement national", "fr", date(2018, 6, 1), None),
    ("Q205150", "National Rally", "en", date(2018, 6, 1), None),
    ("Q23731823", "En marche !", "fr", date(2016, 4, 6), date(2017, 5, 8)),
    ("Q23731823", "La République en marche !", "fr", date(2017, 5, 8), date(2022, 9, 22)),
    ("Q23731823", "Renaissance", "fr", date(2022, 9, 22), None),
    ("Q541679", "Partito Democratico della Sinistra", "it", None, date(1998, 2, 13)),
    ("Q541679", "Democratic Party of the Left", "en", None, date(1998, 2, 13)),
    ("Q541679", "Democratici di Sinistra", "it", date(1998, 2, 13), date(2007, 10, 14)),
    ("Q541679", "Democrats of the Left", "en", date(1998, 2, 13), date(2007, 10, 14)),
]
WIKIDATA_TITLE = "Wikidata items for party names and dates"
RENAMED = {
    "AT:Das Neue Österreich und Liberales Forum": (None, "NEOS"),
    "AT:Die Grünen": (None, "Grüne"),
    "BE:Ecolo": (None, "Ecolo"),
    "BE:Open Vlaamse Liberalen en Democraten": (None, "Open Vld"),
    "BE:VLAAMS BELANG": (None, "VB"),
    "BE:N-VA": (None, "N-VA"),
    "BE:Parti socialiste": ("Socialist Party", "PS"),
    "BE:Christlich-Soziale Partei": ("Christian Social Party", "CSP"),
    "BE:PVDA/PTB": (None, "PVDA–PTB"),
    "BE:Mouvement Réformateur (MR)": (None, "MR"),
    "BE:Christen-Democratisch en Vlaams (CD&V)": (None, "CD&V"),
    "BE:Groen": (None, "Groen"),
    "CY:Δημοκρατικός Συναγερμός (ΔΗΣΥ)": (None, "DISY"),
    "CY:Δημοκρατικό Κόμμα (ΔΗΚΟ)": (None, "DIKO"),
    "CY:Ανορθωτικό Κόμμα Εργαζόμενου Λαού (ΑΚΕΛ)": (None, "AKEL"),
    "CZ:ANO 2011": (None, "ANO"),
    "CZ:Česká pirátská strana": (None, "Piráti"),
    "EE:Sotsiaaldemokraatlik Erakond": ("Social Democratic Party", "SDE"),
    "EE:Eesti Reformierakond": ("Estonian Reform Party", "Reform"),
    "ES:Junts per Catalunya (previously CiU)": (None, "Junts"),
    "HR:Hrvatska demokratska zajednica": (None, "HDZ"),
    "IT:Forza Italia": ("Forza Italia", "FI"),
    "LT:Tėvynės sąjunga – Lietuvos krikščionys demokratai": (
        "Homeland Union – Lithuanian Christian Democrats",
        "TS–LKD",
    ),
    "LT:Lietuvos socialdemokratų partija": ("Social Democratic Party of Lithuania", "LSDP"),
    'LV:Nacionālā apvienība "Visu Latvijai!” – "Tēvzemei un Brīvībai/LNNK': (None, "NA"),
    "NL:Partij voor de Vrijheid": (None, "PVV"),
    "NL:Partij voor de Dieren": (None, "PvdD"),
    "NL:Democraten 66": (None, "D66"),
    "SK:Kresťanskodemokratické hnutie": (None, "KDH"),
    "SK:Smer - sociálna demokracia": (None, "Smer"),
}
OTHERS = {"others", "other"}
MISSING = {".", ""}
UK_PARTIES = {
    "Conservative Party": "conservative",
    "NI Conservatives": "conservative",
    "Labour Party": "labour",
    "Labour": "labour",
    "Liberal Party": "liberal-party-1989",
    "Brexit Party": "reform-uk",
    "Green Party of England and Wales": "green-party",
    "Alliance Party": "alliance-party-of-northern-ireland",
    "CPA": "christian-peoples-alliance",
    "BNP": "british-national-party",
    "Ulster Popular Unionist Party": "ulster-popular-unionist",
    "Conservatives": "conservative",
    "Pirate": "pirate-party-uk",
    "Change UK": "the-independent-group-for-change",
    "Unionist Party": "unionist-party-ni",
    "Pensiones": "pensioners-party",
}
CORRECTED = {
    (1984, False, "Alliance Party"): (
        "sdp-liberal-alliance",
        None,
        (
            'The source labels this candidate "Alliance Party"; in Great Britain that is the SDP–Liberal Alliance, not '
            "the Alliance Party of Northern Ireland."
        ),
    ),
    (1989, True, "Labour Party"): (
        None,
        None,
        (
            'The source labels this candidate "Labour Party"; the Labour Party did not contest elections in Northern '
            "Ireland, so no party is linked."
        ),
    ),
    (1989, True, "Green Party"): ("green-party-northern-ireland", None, None),
    (2004, True, "Green Party"): ("green-party-northern-ireland", None, None),
    (2014, True, "Green Party"): ("green-party-northern-ireland", None, None),
    (2019, True, "Green Party of England and Wales"): (
        "green-party-northern-ireland",
        "Green Party Northern Ireland",
        (
            'The source labels this list "Green Party of England and Wales"; in Northern Ireland it was the Green Party '
            "Northern Ireland."
        ),
    ),
    (2019, True, "Conservative Party"): (
        "traditional-unionist-voice",
        "Traditional Unionist Voice",
        (
            'The source labels this list "Conservative Party"; its 62,683 votes are those of the Traditional Unionist '
            "Voice (Jim Allister)."
        ),
    ),
    (2019, True, "Social Democratic Party"): (
        "social-democratic-and-labour-party",
        "Social Democratic and Labour Party",
        (
            'The source labels this list "Social Democratic Party"; its 78,589 votes are those of the Social Democratic '
            "and Labour Party."
        ),
    ),
}
UK_SYSTEMS = {
    "ep-list": ("Regional closed lists, d'Hondt (European Parliament, Great Britain)", {"formula": "dhondt"}),
    "ep-stv": (
        "Single transferable vote (European Parliament, Northern Ireland)",
        {"formula": "stv", "quota": "droop"},
    ),
}
WINDOW_CAVEAT = (
    "The source gives only the year of the election. The dates are the European Parliament's voting window for that "
    "year, not the day this country voted."
)
EUC_NAMES = {
    (1979, "Cotswolds"): "The Cotswolds",
    (1979, "Tyne South and Wear"): "South Tyne and Wear",
    (1979, "South East Wales"): "South-East Wales",
    (1984, "Cotswolds"): "The Cotswolds",
    (1984, "Oxfordshire and Buckinghamshire"): "Oxford and Buckinghamshire",
    (1984, "Sussex East"): "East Sussex",
    (1984, "Sussex West"): "West Sussex",
}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "-".join(re.findall(r"[a-z0-9]+", plain))


def wikidata_items(fetching: bool) -> tuple[pl.DataFrame, pl.DataFrame]:
    database = sqlite3.connect(raw.cached(parlgov.SOURCE, parlgov.DATABASE).path)
    results = pl.read_database(
        """select cast(substr(e.date, 1, 4) as integer) year, k.name_short iso3, r.votes, r.party_id, r.alliance_id,
                  p.wikipedia
           from election e join country k on k.id = e.country_id
           join election_result r on r.election_id = e.id join party p on p.id = r.party_id
           where e.type_id = 1""",
        database,
    ).with_columns(iso=pl.col("iso3").replace_strict(PARLGOV_ISO, default=None))
    matched = (
        pl.read_parquet(PARSED / "results.parquet")
        .filter(pl.col("votes") > 0)
        .group_by("country", "year", "party", "party_original")
        .agg(pl.col("votes").sum())
        .with_columns(
            iso=pl.col("country").replace_strict(ISO, default=None),
            label=pl.when(pl.col("party_original").fill_null("") != "").then("party_original").otherwise("party"),
        )
        .filter(pl.col("iso").is_not_null())
        .join(
            results.filter(pl.col("votes").is_not_null() & pl.col("wikipedia").fill_null("").str.starts_with("http")),
            on=["iso", "year", "votes"],
        )
    )
    members = (
        matched.select("iso", "year", "label", "alliance_id")
        .drop_nulls("alliance_id")
        .join(
            results.select("alliance_id", member=pl.col("party_id"), member_wikipedia=pl.col("wikipedia")),
            on="alliance_id",
        )
    )
    hosts: dict[str, dict[int, str]] = defaultdict(dict)
    for party, article in (
        *matched.select("party_id", "wikipedia").unique().iter_rows(),
        *members.select("member", "member_wikipedia").unique().iter_rows(),
    ):
        if article is None:
            continue
        link = urlparse(article.strip())
        if not link.netloc.endswith(".wikipedia.org"):
            continue
        hosts[link.netloc][party] = unquote(link.path.removeprefix("/wiki/")).replace("_", " ")
    items: dict[int, str] = {}
    for host, titles in sorted(hosts.items()):
        pairs = sorted(titles.items())
        for start in range(0, len(pairs), 50):
            chunk = pairs[start : start + 50]
            url = f"https://{host}/w/api.php?" + urlencode(
                {
                    "action": "query",
                    "prop": "pageprops",
                    "ppprop": "wikibase_item",
                    "redirects": 1,
                    "format": "json",
                    "titles": "|".join(title for _, title in chunk),
                }
            )
            if fetching:
                try:
                    raw.cached(SOURCE, url)
                except LookupError:
                    raw.fetch(SOURCE, url, suffix=".json")
            query = json.loads(raw.cached(SOURCE, url).path.read_text())["query"]
            renamed = {entry["from"]: entry["to"] for entry in query.get("normalized", []) + query.get("redirects", [])}
            pages = {page["title"]: page.get("pageprops", {}).get("wikibase_item") for page in query["pages"].values()}
            for party, title in chunk:
                while title in renamed:
                    title = renamed[title]
                if pages.get(title) is not None:
                    items[party] = pages[title]
    return (
        matched.select("iso", "label", item=pl.col("party_id").replace_strict(items, default=None)).drop_nulls("item"),
        members.select(
            "iso",
            "year",
            "label",
            "alliance_id",
            item=pl.col("member").replace_strict(items, default=None),
            member=pl.col("member"),
        ),
    )


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xlsx")
    raw.fetch(SOURCE, DEMON, suffix=".html")
    wikidata_items(True)
    for item in NAMED_ITEMS:
        try:
            raw.cached(SOURCE, ENTITY_URL.format(item))
        except LookupError:
            raw.fetch(SOURCE, ENTITY_URL.format(item), suffix=".json")


def parse() -> None:
    artifact = raw.cached(SOURCE, URL)
    frame = pl.read_excel(artifact.path)
    count = pl.col("votes_total").str.strip_chars()
    eligible = pl.col("eligible").str.strip_chars()
    frame = frame.with_columns(
        pl.col("country").str.strip_chars(),
        pl.col("constituency").str.strip_chars(),
        pl.col("party").str.strip_chars(),
        pl.col("party_original").str.strip_chars(),
        cast=pl.when(count.str.contains(r"^\d+$")).then(count.cast(pl.Int64, strict=False)),
        registered=pl.when(eligible.str.contains(r"^\d+$")).then(eligible.cast(pl.Int64, strict=False)),
    )
    sums = frame.group_by("country", "constituency", "year").agg(
        pl.col("seats").sum().alias("allocated"), pl.col("seats_total").first().alias("declared")
    )
    wrong = sums.filter(pl.col("allocated") != pl.col("declared"))
    if wrong.height:
        raise ValueError(f"seats differ from the constituency total:\n{wrong}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        "country",
        "constituency",
        "year",
        "party",
        "party_original",
        "seats_total",
        "seats",
        "registered",
        pl.col("cast").alias("ballots_cast"),
        "votes_valid",
        "votes",
        pl.col("ep_affiliation").str.strip_chars(),
    ).write_parquet(PARSED / "results.parquet")


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    results = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="European Parliament elections 1979–2019 by member state and constituency, with votes and seats "
            "by national party, from Caramani's EUI Cadmus dataset",
            polity="EU",
        )
        union = cs.polity("EU")
        parliament = cs.key("eu.body", "european-parliament")
        if {union, parliament} & cs.minted:
            raise ValueError("publish eu.eurlex before the historical European Parliament elections")
        source = cs.source("European University Institute", "EP election results at constituency level, 1979–2019")
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher="European University Institute",
            title="Elections to the European Parliament, 1979–2019: results at constituency level",
            url="https://hdl.handle.net/1814/75475",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
        )
        cs.artifact(artifact, source)

        def name(
            entity: UUID,
            text: str,
            lang: str = "en",
            kind: str = "official",
            preferred: bool = True,
            id: UUID | None = None,
        ) -> None:
            cs.add(
                "name",
                id=id,
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=preferred,
                valid_from=None,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
                source_id=None,
            )

        polities = {iso: cs.polity(iso) for iso in ISO.values()}
        systems = {code: cs.key("gb.electoral_system", code) for code in UK_SYSTEMS}
        for code, (label, rules) in UK_SYSTEMS.items():
            if systems[code] in cs.minted:
                cs.add("electoral_system", id=systems[code], polity_id=polities["GB"], rules=rules, rules_version=1)
                name(systems[code], label)
        fptp = cs.key("gb.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("first past the post must exist")
        kingdom = cs.area("gb.nation", "GBR")
        if kingdom in cs.minted:
            raise ValueError("the United Kingdom's area must already exist")
        cs.add(
            "area_parent",
            area_id=cs.area("eu.member_state", "GB"),
            parent_id=kingdom,
            valid_from=date(1973, 1, 1),
            valid_from_precision="day",
            valid_to=date(2020, 1, 31),
            valid_to_precision="day",
        )
        gb_parties = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'gb.party'").fetchall()
        }
        uk_parties = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'eu.ep_cadmus.party' and value like 'GB:%%'"
            ).fetchall()
        }
        votes_source = cs.source(parlgov.PUBLISHER, parlgov.TITLE)
        if votes_source in cs.minted:
            raise ValueError("publish parlgov before linking the European Parliament parties")
        cs.artifact(raw.cached(parlgov.SOURCE, parlgov.DATABASE), votes_source)
        cadmus_parties = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'eu.ep_cadmus.party'"
            ).fetchall()
        }
        by_item: dict[str, set[UUID]] = defaultdict(set)
        for item, entity in conn.execute(
            """select x.value, x.entity_id from external_id x join party p on p.id = x.entity_id
               join entity e on e.id = x.entity_id where x.scheme = 'wikidata' and e.retired_by is null"""
        ).fetchall():
            by_item[item].add(entity)
        party_names: dict[UUID, set[str]] = defaultdict(set)
        for entity, text in conn.execute(
            "select n.entity_id, n.text from name n join party p on p.id = n.entity_id"
        ).fetchall():
            party_names[entity].add(fold(text))
        labels: dict[str, set[str]] = defaultdict(set)
        parlgov_items, parlgov_members = wikidata_items(False)
        for iso, label, item in parlgov_items.iter_rows():
            labels[f"{iso}:{label}"].add(item)
        unambiguous = {
            label: next(iter(items)) for label, items in labels.items() if len(items) == 1 and label in cadmus_parties
        }
        sharing: dict[tuple[str, str], list[str]] = defaultdict(list)
        for label, item in unambiguous.items():
            sharing[label[:2], item].append(label)
        linked = {"merged": 0, "attached": 0, "coalitions left": 0}
        for (iso, item), group in sharing.items():
            national = by_item[item] - set(cadmus_parties.values())
            for label in group:
                entity = cadmus_parties[label]
                if entity in by_item[item] or entity in cs.survivors:
                    continue
                if not national:
                    if len(group) == 1:
                        cs.add("external_id", entity_id=entity, scheme="wikidata", value=item)
                        linked["attached"] += 1
                elif len(national) == 1 and (len(group) == 1 or fold(label[3:]) in party_names[next(iter(national))]):
                    cs.merge(entity, next(iter(national)))
                    linked["merged"] += 1
                else:
                    linked["coalitions left"] += 1
        print(linked)
        attached_items = {item: {cadmus_parties[label]} for label, item in unambiguous.items() if not by_item[item]}
        coalition_members: dict[tuple[str, int, str], set[UUID | None]] = defaultdict(set)
        for iso, year, label, _, item, _ in parlgov_members.iter_rows():
            key = f"{iso}:{label}"
            if (
                key not in cadmus_parties
                or cadmus_parties[key] in cs.survivors
                or str(cadmus_parties[key]) in cs.merged
            ):
                continue
            holders = by_item.get(item, set()) or attached_items.get(item, set())
            coalition_members[iso, year, label].add(
                cs.survivors.get(next(iter(holders)), next(iter(holders))) if len(holders) == 1 else None
            )
        coalitions = {
            key: members for key, members in coalition_members.items() if len(members) > 1 and None not in members
        }

        wikidata = cs.source("Wikidata", WIKIDATA_TITLE)
        if wikidata in cs.minted:
            cs.add(
                "source",
                id=wikidata,
                kind="wikidata",
                publisher="Wikidata",
                title=WIKIDATA_TITLE,
                url="https://www.wikidata.org",
                license="CC0 1.0",
                license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            )
        for item in NAMED_ITEMS:
            cs.artifact(raw.cached(SOURCE, ENTITY_URL.format(item)), wikidata)
        for label, (scheme, value) in SURVIVORS.items():
            entity = cadmus_parties[label]
            survivor = cs.key(scheme, value)
            if entity in cs.survivors or entity == survivor:
                continue
            for dropped in sorted(WRONG_NAMES.get(label, ())):
                found = conn.execute(
                    "select id from name where entity_id = %s and text = %s", [entity, dropped]
                ).fetchone()
                if found is not None:
                    cs.add(
                        "name_drop",
                        name_id=found[0],
                        entity_id=entity,
                        text=dropped,
                        reason="The source's English rendering of the party; the party's own name is already on the surviving entity",
                    )
            cs.merge(entity, survivor)
        for entity in (cadmus_parties.get(label) for label in NO_PARTY):
            if entity is not None and entity not in cs.retired:
                cs.add(
                    "entity_retirement",
                    entity_id=entity,
                    reason="The source's Independents line is a set of independent candidates, not a party; its options load with no party",
                )
        for item, text, lang, start, end in DATED_NAMES:
            holder = cs.key("wikidata", item)
            cs.add(
                "name",
                entity_id=holder,
                text=text,
                lang=lang,
                kind="official",
                is_preferred=True,
                valid_from=start,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="day",
                source_id=wikidata,
            )
        english = dict(
            conn.execute(
                """select n.entity_id, n.id from name n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.kind = 'official' and n.lang = 'en' and n.entity_id = any(%s)""",
                [SOURCE, [cs.key("eu.ep_cadmus.party", label) for label in RENAMED]],
            ).fetchall()
        )
        for label, (text, short) in RENAMED.items():
            party = cs.key("eu.ep_cadmus.party", label)
            if text is not None and party not in cs.minted:
                name(party, text, id=english.get(party))
            name(party, short, "und", "abbreviation", False)
        allied: set[UUID] = set()
        print(f"{len(coalitions)} coalition lists with members named by ParlGov")
        stale = conn.execute(
            """select n.id, n.entity_id from note n join changeset c on c.id = n.changeset_id
               join external_id x on x.entity_id = n.entity_id and x.scheme = 'eu.election' and x.value like 'GB:%%'
               where c.pipeline = %s and n.body_md = %s""",
            [SOURCE, WINDOW_CAVEAT],
        ).fetchall()
        for id, entity in stale:
            cs.add(
                "note_drop",
                note_id=id,
                entity_id=entity,
                body_md=WINDOW_CAVEAT,
                reason="UK polls for the European Parliament were held on a single day, the Thursday of the window",
            )
        retired = {
            value: entity
            for value, entity in conn.execute(
                """select x.value, x.entity_id from external_id x join entity e on e.id = x.entity_id
                   where x.scheme = 'eu.contest' and x.value = any(%s) and e.retired_by is null""",
                [["GB:1994:gb.ep_constituency:scotland", "GB:1994:gb.ep_constituency:wales"]],
            ).fetchall()
        }
        for entity in retired.values():
            cs.add(
                "entity_retirement",
                entity_id=entity,
                reason="Scotland and Wales elected their members in single-member constituencies by first past the post "
                "in 1994; the source's row is a national aggregate, not a contest",
            )
        umbrellas: dict[int, UUID] = {}
        for year, (start, end) in WINDOWS.items():
            umbrella = cs.key("eu.election", f"{start}:european-parliament")
            umbrellas[year] = umbrella
            cs.add(
                "election",
                id=umbrella,
                polity_id=union,
                kind="general",
                date_from=start,
                date_to=end,
                date_precision="day",
                status="held",
            )
            name(umbrella, f"{year} European Parliament election")
            cs.add("slug", path=f"eu/{start}-european-parliament", entity_id=umbrella)

        elections: dict[tuple[str, int], tuple[UUID, str]] = {}
        parties: dict[tuple[str, str], UUID] = {}
        units: dict[tuple[str, str], UUID] = {}
        joined: dict[UUID, dict[date, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
        split: Counter[str] = Counter()
        skipped: dict[str, int] = {}
        seen: dict[str, UUID] = {}
        deferred: list[tuple[date, dict]] = []
        sets = 0
        frames: list[pl.DataFrame] = []
        for (country, year), by_election in results.group_by(["country", "year"], maintain_order=True):
            iso = ISO[country]
            window = WINDOWS.get(year)
            election = cs.key("eu.election", f"{iso}:{year}:european-parliament")
            start = window[0] if window else date(year, 1, 1)
            path = f"{iso.lower()}/{start}-european-parliament"
            cs.add(
                "election",
                id=election,
                polity_id=polities[iso],
                parent_id=umbrellas.get(year),
                kind="general",
                date_from=start,
                date_to=start if iso == "GB" else window[1] if window else date(year, 12, 31),
                date_precision="day" if window else "year",
                original_text=str(year),
                status="held",
            )
            name(election, f"{year} European Parliament election in {country}")
            cs.add("slug", path=path, entity_id=election)
            if iso != "GB":
                cs.add(
                    "note",
                    entity_id=election,
                    kind="caveat",
                    body_md=(
                        WINDOW_CAVEAT
                        if window
                        else "The source gives only the year of the election, which took place between general "
                        "European Parliament elections following the country's accession."
                    ),
                )
            elections[(country, year)] = (election, path)
            for (constituency,), rows in by_election.group_by(["constituency"], maintain_order=True):
                euc = iso == "GB" and year < 1999 and constituency != "Northern Ireland"
                if euc and year not in (1979, 1984):
                    skipped[f"{country} {constituency}"] = skipped.get(f"{country} {constituency}", 0) + 1
                    deferred.extend((start, row) for row in rows.iter_rows(named=True) if row["seats"])
                    continue
                replaced = REPLACED.get((year, constituency)) if iso == "GB" else None
                if replaced is not None:
                    rows = rows.with_columns(
                        votes=pl.col("party_original").replace_strict(replaced[0], return_dtype=pl.Int64),
                        votes_valid=pl.lit(replaced[1], dtype=pl.Int64),
                    )
                college = iso == "BE" or constituency == "Greenland"
                if euc:
                    label = EUC_NAMES.get((year, constituency), constituency)
                    scheme, code = "gb.euc", f"{year}:{slug(label)}"
                    if int(rows["seats"].fill_null(0).sum()) != 1:
                        raise ValueError(f"{year} {constituency}: {rows['seats'].sum()} seats declared, not 1")
                elif constituency == "National":
                    scheme, code, label = "eu.member_state", iso, country
                elif college:
                    scheme, code, label = "eu.member_state", iso, constituency
                else:
                    code = CONSTITUENCIES.get((iso, constituency))
                    if code is None and iso == "PL":
                        code = constituency.split(" ", 1)[0]
                    scheme = f"{iso.lower()}.ep_constituency"
                    label = constituency
                    if code is None:
                        skipped[f"{country} {constituency}"] = skipped.get(f"{country} {constituency}", 0) + 1
                        continue
                area = cs.area(scheme, code)
                if area in cs.minted:
                    skipped[f"{country} {constituency}"] = skipped.get(f"{country} {constituency}", 0) + 1
                    continue
                unit = units.get((scheme, code))
                if unit is None:
                    unit = units[(scheme, code)] = cs.unit(scheme, code)
                    if unit in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polities[iso],
                            scheme_code=scheme,
                            code=code,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                            valid_from=WINDOWS[1979][0],
                        )
                seats_total = int(rows["seats_total"][0])
                contest_key = f"{iso}:{year}:{scheme}:{code}" + (f":{fold(constituency)}" if college else "")
                contest = cs.key("eu.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=parliament,
                    district_area_id=area,
                    magnitude=seats_total,
                    electoral_system_id=(
                        fptp
                        if euc
                        else systems["ep-stv" if code == "northern-ireland" else "ep-list"]
                        if iso == "GB"
                        else None
                    ),
                    default_result_kind="counted",
                )
                title = (
                    f"{year} European Parliament election in {country}"
                    if constituency == "National"
                    else f"{year} European Parliament election in {label}"
                    if iso == "GB"
                    else f"{year} European Parliament election in {country}, {label}"
                )
                stored = (
                    conn.execute(
                        "select id from name where entity_id = %s and is_preferred and kind = 'official' and lang = 'en'"
                        " and text = %s",
                        [contest, f"{year} European Parliament election in {country}, {label}"],
                    ).fetchone()
                    if iso == "GB" and constituency != "National"
                    else None
                )
                name(contest, title, id=None if stored is None else stored[0])
                cs.add(
                    "slug",
                    path=f"{path}/european-parliament" if constituency == "National" else f"{path}/{fold(label)}",
                    entity_id=contest,
                )
                result_set = cs.key("eu.ep_cadmus.result_set", contest_key)
                listed = int(rows["votes"].sum())
                valid = rows["votes_valid"][0]
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=min(1, listed / valid) if valid else None,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The source omits parties with under 0.5 percent of the vote and no seats, so the listed "
                    "votes may fall short of the valid votes. Votes for the omitted parties are not recorded.",
                )
                if replaced is not None:
                    demon = cs.source(
                        "Election Demon", "United Kingdom European Parliamentary Election results 1979-99: London"
                    )
                    cs.add(
                        "source",
                        id=demon,
                        kind="aggregator",
                        publisher="Election Demon",
                        title="United Kingdom European Parliamentary Election results 1979-99: London",
                        url="http://www.election.demon.co.uk/eplon.html",
                        notes="David Boothroyd's compilation of the returning officers' declarations (archived copy).",
                    )
                    cs.artifact(raw.cached(SOURCE, DEMON), demon)
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=replaced[2])
                    cs.add("citation", entity_id=result_set, source_id=demon)
                values = []
                for position, row in enumerate(rows.iter_rows(named=True), start=1):
                    label_text = row["party_original"] or row["party"]
                    shown = row["party"] if iso == "GB" and row["party_original"] in MISSING else label_text
                    other = row["party"].lower() in OTHERS
                    party = None
                    fix = CORRECTED.get((year, constituency == "Northern Ireland", label_text)) if iso == "GB" else None
                    if fix is not None:
                        if fix[1] is not None:
                            shown = fix[1]
                        if fix[0] is not None:
                            party = parties.get((iso, fix[0]))
                            if party is None:
                                party = parties[(iso, fix[0])] = cs.key("gb.party", fix[0])
                                if party in cs.minted:
                                    raise ValueError(f"no gb.party {fix[0]}")
                    elif iso == "GB" and not other and not label_text.startswith("Independent"):
                        labels = (label_text, row["party"])
                        umit_key = next(
                            (
                                key
                                for key in (
                                    *(UK_PARTIES.get(text) for text in labels),
                                    "liberal" if label_text == "Liberal Democrats" and year < 1988 else None,
                                    *(umit.ALIASES.get(text) for text in labels),
                                    *(slug(text) + suffix for suffix in ("", "-party") for text in labels),
                                )
                                if key is not None and (key in umit.EXISTING or key in gb_parties or key == "liberal")
                            ),
                            slug(row["party"]),
                        )
                        party = parties.get((iso, umit_key))
                        if party is None:
                            party = parties[(iso, umit_key)] = (
                                cs.key(*umit.EXISTING[umit_key])
                                if umit_key in umit.EXISTING
                                else cs.key("gb.party", umit_key)
                            )
                            if party in cs.minted:
                                cs.add("party", id=party, kind="party", polity_id=polities[iso])
                                name(party, umit.NAMES.get(umit_key, row["party"]))
                        old = uk_parties.get(f"GB:{label_text}")
                        if old is not None and old != party and old not in cs.survivors and umit_key != "liberal":
                            cs.merge(old, party)
                        if code == "scotland" and year >= 1990 and umit_key == "green-party":
                            party = cs.key("gb.party", "scottish-greens")
                    elif not other and iso != "GB" and f"{iso}:{label_text}" not in NO_PARTY:
                        key = (iso, label_text)
                        party = parties.get(key)
                        if party is None:
                            party = parties[key] = cs.key("eu.ep_cadmus.party", f"{iso}:{label_text}")
                            if party in cs.minted:
                                cs.add("party", id=party, polity_id=polities[iso])
                                name(party, RENAMED.get(f"{iso}:{label_text}", (None,))[0] or row["party"])
                                if row["party_original"] and row["party_original"] != row["party"]:
                                    name(party, row["party_original"], "und", "native", False)
                    if iso == "IT" and year >= 2018 and label_text in ("Lega", "Lega Nord"):
                        party = cs.key("wikidata", "Q85776185")
                    if iso == "GB" and party is not None:
                        seen.setdefault(label_text, party)
                    members = coalitions.get((iso, year, label_text))
                    alliance = None
                    if members is not None:
                        alliance = cs.key("eu.ep_cadmus.alliance", f"{iso}:{year}:{label_text}")
                        if alliance not in allied:
                            allied.add(alliance)
                            cs.add("alliance", id=alliance, kind="electoral_alliance", election_id=election)
                            name(alliance, label_text)
                            for member in sorted(members, key=str):
                                cs.add("alliance_member", alliance_id=alliance, party_id=member, role=None)
                            cs.add("citation", entity_id=alliance, source_id=votes_source)
                    option = cs.key("eu.ep_cadmus.option", f"{contest_key}:{fold(label_text) or label_text}:{position}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if other else "candidate" if euc else "list",
                        party_id=party,
                        alliance_id=alliance,
                        ballot_position=position,
                        ballot_label=shown,
                    )
                    if fix is not None and fix[2] is not None:
                        cs.add("note", entity_id=option, kind="context", body_md=fix[2])
                    if row["seats"]:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="elected" if euc else "won",
                            seats=row["seats"],
                        )
                    if row["seats"]:
                        label_group = row["ep_affiliation"] or ""
                        group = (
                            ("EUL" if year < 1994 else "The Left") if label_group == "GUE" else GROUPS.get(label_group)
                        )
                        if group is None and label_group not in MISSING:
                            split[label_group] += row["seats"]
                        for member in [party] if party is not None else sorted(members or (), key=str):
                            if group is not None and member is not None:
                                joined[member][start][group] += row["seats"]
                    values.append((str(option), 1, row["votes"]))
                    if row["seats"] is not None:
                        values.append((str(option), 16, row["seats"]))
                accounting = [
                    (None, 2, rows["registered"][0]),
                    (None, 3, rows["ballots_cast"][0]),
                    (None, 4, valid if valid is None or listed <= valid else None),
                ]
                values += [value for value in accounting if value[2] is not None]
                sets += 1
                frames.append(
                    pl.DataFrame(
                        {
                            "result_set_id": [str(result_set)] * len(values),
                            "unit_id": [str(unit)] * len(values),
                            "option_id": [value[0] for value in values],
                            "channel": [0] * len(values),
                            "measure": [value[1] for value in values],
                            "count_round": [0] * len(values),
                            "flags": [0] * len(values),
                            "value": [value[2] for value in values],
                        },
                        schema={
                            "result_set_id": pl.String,
                            "unit_id": pl.String,
                            "option_id": pl.String,
                            "channel": pl.Int16,
                            "measure": pl.Int16,
                            "count_round": pl.Int16,
                            "flags": pl.Int16,
                            "value": pl.Float64,
                        },
                    ),
                )
        day, electors, valid, candidates = BY_ELECTION
        by_election = cs.key("eu.election", f"GB:{day}:european-parliament-by-election")
        cs.add(
            "election",
            id=by_election,
            polity_id=polities["GB"],
            parent_id=None,
            kind="by_election",
            date_from=day,
            date_to=day,
            date_precision="day",
            original_text=None,
            status="held",
        )
        name(by_election, "1979 London South West European Parliament by-election")
        by_path = f"gb/{day}-european-parliament-by-election"
        cs.add("slug", path=by_path, entity_id=by_election)
        cs.add(
            "note",
            entity_id=by_election,
            kind="context",
            body_md="Held after Shelagh Roberts, elected in June 1979, was disqualified for her membership of the "
            "Occupational Pensions Board; she resigned from the board and won the by-election.",
        )
        demon = cs.source("Election Demon", "United Kingdom European Parliamentary Election results 1979-99: London")
        scheme, code = "gb.euc", "1979:london-south-west"
        contest_key = f"GB:{day}:{scheme}:{code}"
        contest = cs.key("eu.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=by_election,
            kind="body_seats",
            body_id=parliament,
            district_area_id=cs.area(scheme, code),
            magnitude=1,
            electoral_system_id=fptp,
            default_result_kind="counted",
        )
        name(contest, "1979 London South West European Parliament by-election")
        cs.add("slug", path=f"{by_path}/london-south-west", entity_id=contest)
        result_set = cs.key("eu.ep_cadmus.result_set", contest_key)
        cs.add(
            "result_set",
            id=result_set,
            election_id=by_election,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=demon,
            is_default=True,
            completeness=1,
        )
        values = [(None, 2, electors), (None, 4, valid)]
        for position, (full, ballot, person_key, party_key, votes) in enumerate(candidates, start=1):
            option = cs.key("eu.ep_cadmus.option", f"{contest_key}:{slug(full)}")
            party = None
            if party_key is not None:
                party = (
                    cs.key(*umit.EXISTING[party_key]) if party_key in umit.EXISTING else cs.key("gb.party", party_key)
                )
                if party in cs.minted:
                    cs.add("party", id=party, kind="party", polity_id=polities["GB"])
                    name(party, "Anti-Common Market and Free Trade Party")
            person = (
                cs.key(*person_key) if person_key is not None else cs.key("eu.ep_cadmus.person", f"{day}:{slug(full)}")
            )
            if person in cs.minted:
                if person_key is not None:
                    raise ValueError(f"no person {person_key}")
                cs.add("person", id=person)
                name(person, full)
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=ballot,
            )
            if full in BY_ELECTION_NOTES:
                cs.add("note", entity_id=option, kind="context", body_md=BY_ELECTION_NOTES[full])
            cs.add(
                "candidacy",
                id=cs.key("eu.ep_cadmus.candidacy", f"{contest_key}:{slug(full)}"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=ballot,
                member_of_party_id=party,
                is_independent=party is None,
            )
            if position == 1:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            values.append((str(option), 1, votes))
        frames.append(
            pl.DataFrame(
                {
                    "result_set_id": [str(result_set)] * len(values),
                    "unit_id": [str(units[(scheme, code)])] * len(values),
                    "option_id": [value[0] for value in values],
                    "channel": [0] * len(values),
                    "measure": [value[1] for value in values],
                    "count_round": [0] * len(values),
                    "flags": [0] * len(values),
                    "value": [value[2] for value in values],
                },
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Float64,
                },
            ),
        )
        unseen = []
        for start, row in deferred:
            party = seen.get(row["party_original"] or row["party"])
            group = GROUPS.get(row["ep_affiliation"] or "")
            if party is None or group is None:
                unseen.append((start.year, row["party"]))
            else:
                joined[party][start][group] += row["seats"]
        print(f"skipped Great Britain rows grouped: {len(deferred) - len(unseen)}; unresolved: {unseen}")
        held = {
            party: low
            for party, low in conn.execute(
                "select a.party_id, min(coalesce(a.valid_from, '0001-01-01'::date)) from party_affiliation a"
                " join changeset c on c.id = a.changeset_id where a.kind = 'ep_group' and c.pipeline <> %s group by 1",
                [SOURCE],
            ).fetchall()
        }
        groups: dict[str, UUID] = {}
        for key in {*GROUPS.values(), "EUL", "The Left"}:
            groups[key] = cs.key("ep.group", key)
            if groups[key] in cs.minted:
                cs.add("party", id=groups[key], polity_id=union, kind="parliamentary_group")
        for key, first, last, text, short in GROUP_NAMES:
            for kind, label in (("official", text), ("abbreviation", short)):
                cs.add(
                    "name",
                    entity_id=groups[key],
                    text=label,
                    lang="en",
                    kind=kind,
                    is_preferred=kind == "official",
                    valid_from=date(first, 1, 1),
                    valid_from_precision="year",
                    valid_to=None if last is None else date(last, 1, 1),
                    valid_to_precision="year",
                    source_id=source,
                )
        clipped = []
        affiliations = 0
        for party, terms in joined.items():
            segments: list[tuple[date, date, str]] = []
            for start, tally in sorted(terms.items()):
                end = min(next(day for day in GENERALS if day > start), held.get(party, date.max))
                if end <= start:
                    clipped.append(party)
                    continue
                group = tally.most_common(1)[0][0]
                if segments and segments[-1][2] == group and segments[-1][1] == start:
                    segments[-1] = (segments[-1][0], end, group)
                else:
                    segments.append((start, end, group))
            for start, end, group in segments:
                affiliations += 1
                cs.add(
                    "party_affiliation",
                    party_id=party,
                    parent_party_id=groups[group],
                    kind="ep_group",
                    valid_from=start,
                    valid_from_precision="unknown",
                    valid_to=end,
                    valid_to_precision="unknown",
                )
        print(f"ep_group affiliations: {affiliations}; split lists left ungrouped: {dict(split)}; clipped: {clipped}")
        cs.frame("result_value", pl.concat(frames))
        print(f"{sets} contests, {len(elections)} elections, {len(parties)} parties, skipped {skipped}")
        return cs.write()
