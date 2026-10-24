import csv
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.notional"
VERSION = "1"
VOTES, REGISTERED, VALID = 1, 2, 4
ESTIMATED = 2
DAY = date(2019, 12, 12)
URL = "https://raw.githubusercontent.com/ukparliament/psephology/main/db/data/results-by-parliament/58/notional-general-election/results.csv"
TITLE = "Notional results of the 2019 general election on the 2024 constituency boundaries"
PUBLISHED = {"Con": 372, "Lab": 200, "SNP": 48, "LD": 8, "DUP": 8, "SF": 7, "PC": 2, "SDLP": 2, "Green": 1, "APNI": 1}
AGGREGATES = {"TOTOTHV", "MAXOTHV"}
RECODED = {
    "S14000006": "S14000107",
    "S14000008": "S14000108",
    "S14000010": "S14000109",
    "S14000040": "S14000110",
    "S14000058": "S14000111",
}
NATIONS = {"England": "ENG", "Scotland": "SCT", "Wales": "WLS", "Northern Ireland": "NIR"}
GREENS = {
    "ENG": "Green Party",
    "WLS": "Green Party",
    "SCT": "Scottish Green Party",
    "NIR": "Green Party Northern Ireland",
}
PARTIES = {
    "ADVV": "Advance Together",
    "AGSV": "Alliance for Green Socialism",
    "ANWPV": "Animal Welfare Party",
    "AONTV": "Aontú",
    "APNIV": "Alliance Party of Northern Ireland",
    "ASHV": "Ashfield Independents",
    "BNPV": "British National Party",
    "BPIV": "Burnley and Padiham Independent Party",
    "BRXV": "Reform UK",
    "BSJPV": "Birkenhead Social Justice Party",
    "CFV": "Cumbria First",
    "CHPV": "Christian Party, Proclaiming Christ's Lordship",
    "CMEPV": "Church of the Militant Elvis Party",
    "CMUKV": "The Citizens Movement Party UK",
    "CMUV": "Communities United Party",
    "COMLV": "Communist League",
    "CONV": "Conservative Party",
    "CPAV": "Christian Peoples Alliance",
    "CPV": "The Common People",
    "CVPV": "The Cynon Valley Party",
    "Commons Speaker": "Speaker",
    "DUPV": "Democratic Unionist Party",
    "EDV": "English Democrats",
    "GWLDV": "Gwlad Gwlad",
    "HWDIV": "Heavy Woollen District Independents",
    "INETV": "Independent Network",
    "JACV": "The Justice & Anti-Corruption Party",
    "LABV": "Labour Party",
    "LBTV": "Libertarian Party",
    "LDV": "Liberal Democrats",
    "LIBV": "Liberal Party",
    "LNINV": "Lincolnshire Independents Lincolnshire First",
    "LUTNV": "The Best for Luton Party",
    "MKV": "Mebyon Kernow",
    "MRLPV": "Official Monster Raving Loony Party",
    "MTHRV": "Motherworld Party",
    "NEPV": "The North East Party",
    "PATRV": "Patria",
    "PBPV": "People Before Profit Alliance",
    "PCV": "Plaid Cymru",
    "PFPV": "Psychedelic Movement",
    "POSV": "Proud of Oldham & Saddleworth",
    "PPNVV": "The Peace Party - Non-violence, Justice, Environment",
    "RBDV": "Rebooting Democracy",
    "RENV": "Renew",
    "SDLPV": "Social Democratic and Labour Party",
    "SDPV": "Social Democratic Party",
    "SEPV": "Socialist Equality Party",
    "SFPV": "Scottish Family Party",
    "SFV": "Sinn Féin",
    "SHRPV": "Shropshire Party",
    "SLPV": "Socialist Labour Party",
    "SNAVV": "Space Navies Party",
    "SNPV": "Scottish National Party",
    "SOCV": "The Socialist Party of Great Britain",
    "TCRPV": "The Constitution and Reform Party",
    "TIGV": "The Independent Group for Change",
    "TLWV": "Touch Love Worldwide (UK)",
    "UGPV": "The Universal Good Party",
    "UKIPV": "UK Independence Party",
    "UUPV": "Ulster Unionist Party",
    "VPPV": "Veterans and People's Party",
    "WEPV": "Women's Equality Party",
    "WRPV": "Workers Revolutionary Party",
    "WYV": "Wycombe Independents",
    "YESHV": "Yeshua",
    "YPPV": "Young People's Party",
    "YRKSV": "The Yorkshire Party",
}
METHOD = (
    "A notional result estimates how the 2019 general election would have gone had it been fought on the constituencies "
    "first used in 2024. Colin Rallings and Michael Thrasher produced the estimates for England and Wales, David Denver "
    "for Scotland, and Nicholas Whyte for Northern Ireland, for BBC News, ITV News, Sky News, and the Press "
    "Association, working from the 2019 results and local election results; votes were never counted on these "
    "boundaries. Parliament's election results service and the House of Commons Library publish them and measure "
    "the 2024 gains and losses against them."
)
SNP_NOTE = (
    "These estimates give the SNP 48 of Scotland's 57 seats, so its nine seats in 2024 are 39 fewer, as the House of "
    "Commons Library's results briefing (CBP-10009) gives for Scotland. Some broadcast and newspaper tallies give 38, "
    "because they compare seats whose boundaries did not change with the party defending them at "
    "dissolution rather than with the notional result."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")


def emit() -> Path:
    rows = [row for row in csv.reader(raw.cached(SOURCE, URL).path.open()) if row[11] not in AGGREGATES]
    seats: dict[str, list[list[str]]] = {}
    for row in rows:
        seats.setdefault(RECODED.get(row[2], row[2]), []).append(row)
    if len(seats) != 650:
        raise ValueError(f"{len(seats)} constituencies, not 650")
    won: dict[str, int] = {}
    for code, lines in seats.items():
        if sum(int(line[12]) for line in lines) != int(lines[0][9]):
            raise ValueError(f"{code}: the votes do not sum to the valid votes")
        winner = max(lines, key=lambda line: int(line[12]))[13]
        won[winner] = won.get(winner, 0) + 1
    if {party: won.get(party, 0) for party in PUBLISHED} != PUBLISHED:
        raise ValueError(f"notional seats differ from the published totals: {won}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Notional results of the 2019 general election on the 2024 constituencies, by party, from the "
            "estimates by Rallings and Thrasher, Denver, and Whyte that Parliament publishes",
            polity="GB",
        )
        gb = cs.polity("GB")
        election = cs.key("gb.election", str(DAY))
        if election in cs.minted:
            raise ValueError("no 2019 general election")
        current = {
            code: (area, kind, body, system, magnitude, result_kind)
            for code, area, kind, body, system, magnitude, result_kind in conn.execute(
                """select ac.code, c.district_area_id, c.kind, c.body_id, c.electoral_system_id, c.magnitude,
                          c.default_result_kind
                   from contest c join election e on e.id = c.election_id
                   join area_code ac on ac.area_id = c.district_area_id
                   where e.kind = 'general' and e.date_from = '2024-07-04' and e.polity_id = %s and not c.notional""",
                [gb],
            ).fetchall()
        }
        if set(current) != set(seats):
            raise ValueError(f"constituencies not in 2024: {set(seats) ^ set(current)}")
        parties = {
            text: party
            for text, party in conn.execute(
                """select distinct n.text, o.party_id from contest_option o join contest c on c.id = o.contest_id
                   join name n on n.entity_id = o.party_id and n.is_preferred where c.election_id = %s""",
                [election],
            ).fetchall()
        }
        missing = {*PARTIES.values(), *GREENS.values()} - set(parties)
        if missing:
            raise ValueError(f"parties not on the 2019 ballot: {missing}")
        if conn.execute("select 1 from scheme where code = 'gb.pcon_2024'").fetchone() is None:
            cs.add(
                "scheme",
                code="gb.pcon_2024",
                polity_id=gb,
                kind="electoral_district",
                rank=1,
                noun="2024 constituency",
            )
        source = cs.source("UK Parliament", TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher="UK Parliament",
            title=TITLE,
            url=URL,
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            attribution="Notional 2019 results by Colin Rallings and Michael Thrasher, David Denver (Scotland), and "
            "Nicholas Whyte (Northern Ireland) for BBC News, ITV News, Sky News, and the Press Association, as "
            "published by UK Parliament and the House of Commons Library.",
            notes="Parliament republishes the estimates under the Open Parliament Licence, which does not cover "
            "third-party rights; the estimates are the authors' and the broadcasters'. The data file is in the "
            "psephology repository behind electionresults.parliament.uk.",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        library = cs.source("House of Commons Library", "General election 2024 results (CBP-10009)")
        cs.add(
            "source",
            id=library,
            kind="official_body",
            publisher="House of Commons Library",
            title="General election 2024 results (CBP-10009)",
            url="https://commonslibrary.parliament.uk/research-briefings/cbp-10009/",
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="Measures the 2024 seat changes against the notional 2019 results.",
        )
        for code, lines in seats.items():
            area, kind, body, system, magnitude, result_kind = current[code]
            nation = NATIONS[lines[0][5]]
            unit = cs.unit("gb.pcon_2024", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=gb,
                    scheme_code="gb.pcon_2024",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                )
                cs.add("name", entity_id=unit, text=lines[0][0], lang="en", kind="official", is_preferred=True)
            notional = cs.key(f"{SOURCE}.contest", code)
            cs.add(
                "contest",
                id=notional,
                election_id=election,
                kind=kind,
                body_id=body,
                district_area_id=area,
                magnitude=magnitude,
                electoral_system_id=system,
                default_result_kind=result_kind,
                notional=True,
            )
            cs.add(
                "name",
                entity_id=notional,
                text=f"2019 general election in {lines[0][0]} (notional)",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            result_set = cs.key(f"{SOURCE}.result_set", code)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=notional,
                stage="notional",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            cs.add("citation", entity_id=result_set, source_id=library)
            if nation == "SCT":
                cs.add("note", entity_id=result_set, kind="context", body_md=SNP_NOTE)

            values: list[tuple[object, int, int]] = []
            values += [(None, REGISTERED, int(lines[0][8])), (None, VALID, int(lines[0][9]))]
            for line in lines:
                independent = line[11].startswith("IND")
                party = None if independent else parties[GREENS[nation] if line[11] == "GRNV" else PARTIES[line[11]]]
                label = (
                    line[14]
                    if not independent
                    else "Independent"
                    if line[11] == "IND1V"
                    else f"Independent {line[11][3]}"
                )
                option = cs.key(f"{SOURCE}.option", f"{code}:{line[11]}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=notional,
                    kind="candidate",
                    party_id=party,
                    ballot_label=label,
                )
                values.append((option, VOTES, int(line[12])))
            for option, measure, amount in values:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=ESTIMATED,
                    value=amount,
                )
        return cs.write()
