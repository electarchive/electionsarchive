import html
import itertools
import re
import time
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.gb import umit
from ea.sources.gb.sp_boundaries import NAMES as BOUNDARY_NAMES
from ea.sources.gb.sp_boundaries import regions_1999
from ea.store import DATA, connect

SOURCE = "gb.scottish_parliament"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
ELECTIONS = {
    1999: date(1999, 5, 6),
    2003: date(2003, 5, 1),
    2007: date(2007, 5, 3),
    2011: date(2011, 5, 5),
    2016: date(2016, 5, 5),
    2021: date(2021, 5, 6),
    2026: date(2026, 5, 7),
}
ERAS = {1999: 1999, 2003: 1999, 2007: 1999, 2011: 2011, 2016: 2011, 2021: 2011, 2026: 2026}
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
EC_ATTRIBUTION = "Contains information from the Electoral Commission licensed under the Open Government Licence v3.0."
ANALYSIS_2011 = "https://archive2021.parliament.scot/Electionresults/2011_ElectionAnalysis.xls"
SOURCES = (
    (
        "page",
        "The Scottish Parliament",
        "Scottish Parliament election results, 1999 to 2026",
        "https://www.parliament.scot/msps/elections",
        "Scottish Parliament Copyright Licence",
        "https://www.parliament.scot/about/copyright",
        "Contains information licensed under the Scottish Parliament Copyright Licence.",
    ),
    (
        "analysis2011",
        "The Scottish Parliament",
        "Scottish Parliament election 2011: election analysis workbook",
        "https://www.parliament.scot/msps/elections",
        "Scottish Parliament Copyright Licence",
        "https://www.parliament.scot/about/copyright",
        "Contains information licensed under the Scottish Parliament Copyright Licence.",
    ),
    (
        "ec2007",
        "The Electoral Commission",
        "Scottish Parliament elections 2007: electorate and turnout by constituency",
        "https://www.electoralcommission.org.uk/",
        "Open Government Licence v3.0",
        OGL,
        EC_ATTRIBUTION,
    ),
    (
        "ec2011",
        "The Electoral Commission",
        "Scottish Parliament election 2011: results flat file",
        "https://www.electoralcommission.org.uk/",
        "Open Government Licence v3.0",
        OGL,
        EC_ATTRIBUTION,
    ),
    (
        "ec2016",
        "The Electoral Commission",
        "Scottish Parliament elections 2016: electoral data and results",
        "https://www.electoralcommission.org.uk/",
        "Open Government Licence v3.0",
        OGL,
        EC_ATTRIBUTION,
    ),
    (
        "emb2021",
        "The Electoral Management Board for Scotland",
        "Scottish Parliamentary election 6 May 2021: full votes and seats by party and candidate",
        "https://www.emb.scot/",
        "Public record",
        None,
        "The Electoral Management Board for Scotland.",
    ),
    (
        "emb2026",
        "The Electoral Management Board for Scotland",
        "Scottish Parliament election 7 May 2026: regional votes in each constituency",
        "https://www.emb.scot/downloads/download/176/scottish-parliament-election-2026-downloads",
        "Public record",
        None,
        "The Electoral Management Board for Scotland.",
    ),
    (
        "emb2026full",
        "The Electoral Management Board for Scotland",
        "Scottish Parliament election 7 May 2026: full analysis of the results (votes and seats)",
        "https://www.emb.scot/downloads/download/176/scottish-parliament-election-2026-downloads",
        "Public record",
        None,
        "The Electoral Management Board for Scotland.",
    ),
)
LISTS_2011 = {
    "Con": "Scottish Conservative and Unionist Party",
    "Lab": "Scottish Labour",
    "LD": "Scottish Liberal Democrats",
    "SNP": "Scottish National Party",
    "Green": "Scottish Green Party",
    "Pensioners": "All Scotland Pensioners Party",
    "UKIP": "UK Independence Party",
    "Socialist Labour": "Socialist Labour",
    "Christian Party": "Scottish Christian Party",
    "BNP": "British National Party",
    "Scottish Socialist": "Scottish Socialist Party",
    "Solidarity": "Solidarity",
}
LISTS_2016 = {
    "CON": "Conservative and Unionist Party",
    "LAB": "Labour Party",
    "LIB": "Liberal Democrats",
    "SNP": "Scottish National Party (SNP)",
    "SGRN": "Scottish Green Party",
    "UKIP": "UK Independence Party (UKIP)",
    "SOL": "Solidarity - Scotland's Socialist Movement",
    "RISE": "RISE - Respect, Independence, Socialism and Environmentalism",
    "CHP": 'Christian Party "Proclaiming Christ\'s Lordship"',
    "WEP": "Women's Equality Party",
    "SLBR": "Scottish Libertarian Party",
    "ABBUP": "A Better Britain - Unionist Party",
    "COMP": "Communist Party of Britain",
    "NF": "National Front",
    "ANWP": "Animal Welfare Party",
    "TUSC": "Trade Unionist and Socialist Coalition",
}
PARTY_KEYS = {
    "natural law party": "natural-law",
    "pro-life alliance": "prolife-alliance",
    "humanist party": "humanist",
    "liberal": "liberal-party-1989",
    "conservative": "conservative",
    "conservative and unionist party": "conservative",
    "scottish conservative and unionist party": "conservative",
    "scottish conservative & unionist party": "conservative",
    "scottish liberal democrat party": "liberal-democrats",
    "socialist labour party": "socialist-labour-party",
    "labour": "labour",
    "labour party": "labour",
    "scottish labour": "labour",
    "scottish labour party": "labour",
    "liberal democrat": "liberal-democrats",
    "liberal democrats": "liberal-democrats",
    "scottish liberal democrats": "liberal-democrats",
    "snp": "scottish-national-party",
    "scottish national party": "scottish-national-party",
    "scottish national party (snp)": "scottish-national-party",
    "green": "scottish-greens",
    "scottish green party": "scottish-greens",
    "ssp": "scottish-socialist-party",
    "scottish socialist": "scottish-socialist-party",
    "scottish socialists": "scottish-socialist-party",
    "scottish socialist party": "scottish-socialist-party",
    "sscu": "scottish-senior-citizens-unity-party",
    "scottish senior citizens unity party": "scottish-senior-citizens-unity-party",
    "uk independence party": "uk-independence-party",
    "uk independence party (ukip)": "uk-independence-party",
    "reform uk": "reform-uk",
    "socialist labour": "socialist-labour-party",
    "communist party of britain": "communist-party-of-britain",
    "alliance to liberate scotland and sovereignty": "alliance-to-liberate-scotland",
    "alliance to liberate scotland for independence": "alliance-to-liberate-scotland",
    "scottish alliance for democracy and freedom": "alliance-for-democracy-and-freedom",
    "workers party of britian": "workers-party-of-britain",
    "workers party - scotland": "workers-party-of-britain",
    "ukip": "uk-independence-party",
    "scottish workers party of britain": "workers-party-of-britain",
    "british unionist party and abolish the scottish parliament party": "british-unionist-party",
    "edinburgh & east lothian people": "edinburgh-and-east-lothian-people",
}
AMS = (
    "The additional member system elects 73 members from single-member constituencies by first past the post and "
    "56 more from eight regional party lists, seven for each region, in a single Parliament of 129. Each voter has "
    "two votes: one for a constituency candidate and one for a party list or an individual candidate in the "
    "region. Each region's seven list seats go to parties by the D'Hondt method, dividing a party's regional list "
    "votes by one more than the number of constituency seats it won in that region, so the list compensates for "
    "disproportion in the constituency results. The rules schema cannot yet express that adjusted divisor, so the "
    "declared seats are stored as published."
)
HISTORY_LISTS = (
    "The regional list votes are the Scottish Parliament's election analysis workbook, sheet Region Vote, summed "
    "over the region's constituencies; the workbook gives them by constituency. The same workbook prints "
    "the regional totals, which these sums reproduce. Independent candidates other than those named are pooled "
    "under Others, and the workbook does not name them. Electorates and rejected papers are the sums of "
    "the constituencies' figures. These figures replace the BBC News and Glasgow Digital Library (Aspect) "
    "regional totals that this archive published earlier."
)
DROP_REASONS = {
    1999: "replaced by the official Region Vote figures, which pool the independents the earlier list split",
    2003: "replaced by the official Region Vote figures, which pool the independents the earlier list split",
    2007: "replaced by the official Region Vote figures, which pool the independents the earlier list split",
    2021: "a party that did not stand in this region, listed with no votes by the source",
    2026: "a member elected from a party's list is not a separate ballot option",
}
NO_LIST_VOTES = (
    "The regional list votes by party for this election have not been loaded yet. The seats shown are the "
    "members the Scottish Parliament's results page declares elected from each region's lists."
)
PAGES = {
    year: f"https://www.parliament.scot/msps/elections/{year}-election-results"
    for year in (1999, 2003, 2007, 2011, 2016, 2021, 2026)
}
EC_2011 = "https://web.archive.org/web/20251202132040if_/https://www.electoralcommission.org.uk/sites/default/files/excel_doc/SP-2011-results-flat-file-WEB.xlsx"
EC_2016 = "https://web.archive.org/web/20250912224401if_/https://www.electoralcommission.org.uk/sites/default/files/2021-04/Electoral-Data-Results-May-2016-Scottish-Parliament-elections_2.xls"
EC_2007 = "https://web.archive.org/web/2015if_/http://www.electoralcommission.org.uk/__data/assets/excel_doc/0006/55590/Scotland-Parl-Elec-2007-Turnout-Electorate-Constituency.xls"
EMB_2021 = "https://www.emb.scot/downloads/file/815/spe21-full-votes-and-seats-by-party-candidate"
EMB_2026 = "https://www.emb.scot/downloads/file/1212/regional-votes-by-constituency"
EMB_2026_FULL = "https://www.emb.scot/downloads/file/1201/full-analysis-of-2026-results"


def fetch() -> None:
    for year, url in PAGES.items():
        raw.fetch(SOURCE, url, suffix=".html")
        time.sleep(1)
    raw.fetch(SOURCE, ANALYSIS_2011, suffix=".xls")
    time.sleep(1)
    raw.fetch(SOURCE, EC_2011, suffix=".xlsx")
    time.sleep(1)
    raw.fetch(SOURCE, EC_2016, suffix=".xls")
    time.sleep(1)
    raw.fetch(SOURCE, EC_2007, suffix=".xls")
    raw.fetch(SOURCE, EMB_2021, suffix=".xlsx")
    raw.fetch(SOURCE, EMB_2026, suffix=".xlsx")
    raw.fetch(SOURCE, EMB_2026_FULL, suffix=".xlsx")


def page_lines(year: int) -> list[str]:
    text = raw.cached(SOURCE, PAGES[year]).path.read_text()
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def parse_pages() -> tuple[pl.DataFrame, pl.DataFrame]:
    candidates = []
    members = []
    for year in PAGES:
        lines = page_lines(year)
        start = lines.index("Aberdeen Central")
        split = next(index for index, line in enumerate(lines) if line == "Region results")
        index = start
        while index < split:
            if len(lines[index]) == 1:
                index += 1
                continue
            constituency = lines[index]
            index += 1
            block = []
            while lines[index] != "MSP elected":
                block.append(lines[index])
                index += 1
            member = lines[index + 1]
            majority = int(re.sub(r"\D", "", lines[index + 2]))
            index += 3
            position = 0
            first = len(candidates)
            while position < len(block):
                name = block[position]
                detail = (
                    block[position + 1] if position + 1 < len(block) and block[position + 1].startswith("- ") else None
                )
                position += 2 if detail is not None else 1
                match = re.fullmatch(r"- (?:(.+?) - )?([\d,]+) votes?", detail) if detail is not None else None
                if match is not None:
                    party, votes = match.group(1), int(match.group(2).replace(",", ""))
                elif detail is not None and not re.search(r"\d", detail):
                    party, votes = detail[2:], None
                else:
                    party, votes = None, None
                candidates.append(
                    {
                        "year": year,
                        "constituency": constituency,
                        "candidate": name,
                        "party": party,
                        "votes": votes,
                        "elected": name == member,
                        "majority": majority if name == member else None,
                    }
                )
            winner = next((row for row in candidates[first:] if row["elected"]), None)
            if winner is not None and winner["votes"] is None:
                rivals = [row["votes"] for row in candidates[first:] if row["votes"] is not None and not row["elected"]]
                if rivals:
                    winner["votes"] = max(rivals) + majority
            if year == 1999 and constituency == "Falkirk West":
                for row in candidates[first:]:
                    if row["candidate"] == "Ross Martin":
                        row["votes"] = 6319
                    if row["elected"]:
                        row["votes"] = 18511
        region = None
        index = split + 2
        while index < len(lines) and not lines[index].startswith("Copyright"):
            line = lines[index]
            if line == "MSPs elected":
                region = lines[index - 1]
                index += 1
                continue
            if (
                index + 1 < len(lines)
                and (lines[index + 1] == "-" or lines[index + 1].startswith("- "))
                and region is not None
            ):
                members.append({"year": year, "region": region, "member": line, "party": lines[index + 1][2:] or None})
                index += 2
                continue
            if index + 1 < len(lines) and lines[index + 1] == "MSPs elected":
                index += 1
                continue
            index += 1
    return pl.DataFrame(candidates), pl.DataFrame(members)


def sheet(path: Path, name: str) -> list[list[str | None]]:
    frame = fastexcel.read_excel(str(path)).load_sheet_by_name(name, header_row=None).to_polars()
    return [[None if cell is None or cell == "" else str(cell).strip() for cell in row] for row in frame.iter_rows()]


def number(cell: str | None) -> int | None:
    return None if cell is None else int(round(float(cell)))


ALIASES = {
    "fifecentral": "centralfife",
    "fifenortheast": "northeastfife",
    "renfrewshirewest": "westrenfrewshire",
    "taysidenorth": "northtayside",
    "aberdeenshirewestandkincardine": "westaberdeenshireandkincardine",
    "nahheilanananiar": "naheileanananiar",
    "orkneyislands": "orkney",
    "shetlandislands": "shetland",
    "glasgowballieston": "glasgowbaillieston",
    "midscotandfife": "midscotlandandfife",
    "southscotland": "southofscotland",
    "southscotand": "southofscotland",
    "westscotland": "westofscotland",
}


def fold(text: str) -> str:
    key = _fold(text)
    return ALIASES.get(key, key)


def _fold(text: str) -> str:
    text = text.lower().replace("&", "and").replace(" s. ", " south ").replace(" n. ", " north ")
    return re.sub(r"[^a-z]+", "", re.sub(r"\bst\.?\b", "st", text))


OFFICIAL_LABELS = {
    "Labour": "Scottish Labour Party",
    "SNP": "Scottish National Party",
    "Liberal Democrat": "Scottish Liberal Democrats",
    "Conservative": "Scottish Conservative and Unionist Party",
    "Green": "Scottish Green Party",
    "Scottish Socialist": "Scottish Socialist Party",
    "Scottish Unionist": "Scottish Unionist Party",
    "Other": "Others",
    "Communist Party of Britian": "Communist Party of Britain",
    "Scottish Senior Citizens Unit Party": "Scottish Senior Citizens Unity Party",
}
PARTY_LABELS = {
    "con": "Scottish Conservative and Unionist Party",
    "lab": "Scottish Labour Party",
    "ld": "Scottish Liberal Democrats",
    "snp": "Scottish National Party",
    "grn": "Scottish Green Party",
}
BUCKETS = {
    "conservative": "con",
    "scottish conservative and unionist party": "con",
    "scottish conservative & unionist party": "con",
    "labour": "lab",
    "scottish labour": "lab",
    "scottish labour party": "lab",
    "liberal democrat": "ld",
    "scottish liberal democrats": "ld",
    "snp": "snp",
    "scottish national party": "snp",
    "scottish green party": "grn",
    "independent": "ind",
}


def parse_workbooks() -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    units = []
    lists = []
    buckets = []
    path = raw.cached(SOURCE, EC_2011).path
    rows = sheet(path, "Constituency results")
    header = rows[0]
    for row in rows[1:]:
        if row[0] is None or row[0] == "Total":
            continue
        units.append(
            {
                "year": 2011,
                "ballot": "constituency",
                "constituency": row[0],
                "region": None,
                "electorate": number(row[1]),
                "cast": None,
                "rejected": number(row[2]),
                "valid": number(row[3]),
            }
        )
        for label, cell in zip(header[6:12], row[6:12], strict=True):
            buckets.append({"year": 2011, "constituency": row[0], "bucket": label.lower(), "votes": number(cell)})
    rows = sheet(path, "List results")
    header = rows[0]
    for row in rows[1:]:
        if row[1] is None or row[1] == "Total":
            continue
        units.append(
            {
                "year": 2011,
                "ballot": "list",
                "constituency": row[1],
                "region": row[0],
                "electorate": number(row[2]),
                "cast": None,
                "rejected": number(row[3]),
                "valid": number(row[4]),
            }
        )
        for label, cell in zip(header[6:], row[6:], strict=True):
            lists.append(
                {"year": 2011, "region": row[0], "constituency": row[1], "party": label, "votes": number(cell)}
            )
    path = raw.cached(SOURCE, EC_2016).path
    for ballot, name in (("constituency", "Constituencies - Results"), ("list", "Regional List - Results")):
        rows = sheet(path, name)
        header = rows[1]
        parties = header[9 : header.index("Others", 9) + 1]
        for row in rows[2:]:
            if row[1] is None or row[0] is None:
                continue
            units.append(
                {
                    "year": 2016,
                    "ballot": ballot,
                    "constituency": row[1],
                    "region": row[2],
                    "electorate": number(row[3]),
                    "cast": number(row[4]),
                    "rejected": number(row[6]),
                    "valid": number(row[5]),
                }
            )
            for label, cell in zip(parties, row[9 : 9 + len(parties)], strict=True):
                if ballot == "list":
                    lists.append(
                        {"year": 2016, "region": row[2], "constituency": row[1], "party": label, "votes": number(cell)}
                    )
                else:
                    buckets.append(
                        {"year": 2016, "constituency": row[1], "bucket": label.lower(), "votes": number(cell)}
                    )
    path = raw.cached(SOURCE, EC_2007).path
    for row in sheet(path, "Sheet1")[3:]:
        if row[1] is None or row[1] == "Scotland" or row[2] in (None, "."):
            continue
        units.append(
            {
                "year": 2007,
                "ballot": "constituency",
                "constituency": row[1],
                "region": None,
                "electorate": number(row[2]),
                "cast": number(row[3]),
                "rejected": None,
                "valid": number(row[4]),
            }
        )
    path = raw.cached(SOURCE, EMB_2021).path
    rows = sheet(path, "Constituency - Votes")
    start = next(index for index, row in enumerate(rows) if row[0] == "Constituency")
    header = rows[start]
    columns = {label: index for index, label in enumerate(header) if label is not None}
    party_columns = {
        "con": "Scottish Conservative and Unionist Party",
        "lab": "Scottish Labour Party",
        "ld": "Scottish Liberal Democrats",
        "snp": "Scottish National Party (SNP)",
        "grn": "Scottish Green Party",
        "ind": "Independent Candidates",
    }
    for row in rows[start + 1 :]:
        if row[0] is None or row[0] == "Total":
            break
        rejected = number(row[22])
        units.append(
            {
                "year": 2021,
                "ballot": "constituency",
                "constituency": row[0],
                "region": None,
                "electorate": number(row[columns["Electorate"]]),
                "cast": number(row[columns["Total Votes"]]),
                "rejected": rejected,
                "valid": number(row[columns["Total Votes"]]) - rejected,
            }
        )
        for bucket, label in party_columns.items():
            votes = number(row[columns[label]]) or 0
            if bucket == "ind":
                votes += number(row[columns["Candidate - No description"]]) or 0
            buckets.append({"year": 2021, "constituency": row[0], "bucket": bucket, "votes": votes})
    rows = sheet(path, "Region - Votes")
    start = next(index for index, row in enumerate(rows) if row[0] == "Region")
    regions = rows[start][1:-2]
    totals = {}
    for row in rows[start + 1 :]:
        if row[0] is None or row[0] in ("Registered Parties", "Individual Candidates"):
            continue
        if row[0] in ("Rejected Papers", "Total", "Electorate"):
            totals.setdefault(row[0], row[1 : 1 + len(regions)])
            continue
        if row[0].startswith("Turnout"):
            break
        for region, cell in zip(regions, row[1 : 1 + len(regions)], strict=True):
            lists.append({"year": 2021, "region": region, "constituency": None, "party": row[0], "votes": number(cell)})
    for index, region in enumerate(regions):
        units.append(
            {
                "year": 2021,
                "ballot": "list",
                "constituency": None,
                "region": region,
                "electorate": number(totals["Electorate"][index]),
                "cast": number(totals["Total"][index]),
                "rejected": number(totals["Rejected Papers"][index]),
                "valid": number(totals["Total"][index]) - number(totals["Rejected Papers"][index]),
            }
        )
    path = raw.cached(SOURCE, ANALYSIS_2011).path
    for year, suffix in ((1999, "99"), (2003, "03"), (2007, "07")):
        rows = sheet(path, f"Region Vote {suffix}")
        start = next(index for index, row in enumerate(rows) if row[0] == "Region")
        header = rows[start]
        spoilt, total, electorate = (header.index(label) for label in ("Spoilt Papers", "Total Votes", "Electorate"))
        labels = []
        for index in range(4, spoilt):
            label = header[index]
            if label == "Do not use":
                labels.append(None)
            elif label.startswith("Independent"):
                labels.append(
                    f"Independent candidate {sum(1 for other in labels if other and other.startswith('Independent')) + 1}"
                )
            else:
                labels.append(OFFICIAL_LABELS.get(label, label))
        regions: dict[str, dict[str, int]] = {}
        for row in rows[start + 1 :]:
            if row[0] is None or row[1] is None or not row[1].isdigit():
                continue
            figures = regions.setdefault(row[0], {})
            votes = [number(row[index]) or 0 for index in range(4, spoilt)]
            if sum(votes) != number(row[total]):
                raise ValueError(f"{year} {row[2]}: list votes do not sum to the printed total")
            for label, count in zip(labels, votes, strict=True):
                if label is not None:
                    figures[label] = figures.get(label, 0) + count
            for field, index in (("electorate", electorate), ("rejected", spoilt), ("valid", total)):
                figures[f"\0{field}"] = figures.get(f"\0{field}", 0) + (number(row[index]) or 0)
        for region, figures in regions.items():
            for label, count in figures.items():
                if not label.startswith("\0") and count > 0:
                    lists.append({"year": year, "region": region, "constituency": None, "party": label, "votes": count})
            units.append(
                {
                    "year": year,
                    "ballot": "list",
                    "constituency": None,
                    "region": region,
                    "electorate": figures["\0electorate"],
                    "cast": figures["\0valid"] + figures["\0rejected"],
                    "rejected": figures["\0rejected"],
                    "valid": figures["\0valid"],
                }
            )
    rows = sheet(raw.cached(SOURCE, EMB_2026_FULL).path, "Constituency - Votes")
    start = next(index for index, row in enumerate(rows) if row[0] == "Constituency")
    columns = {label.strip(): index for index, label in enumerate(rows[start]) if label is not None}
    for row in rows[start + 1 :]:
        if row[0] is None or row[0] == "Total":
            break
        rejected = number(row[columns["Rejected Ballot Papers"]])
        units.append(
            {
                "year": 2026,
                "ballot": "constituency",
                "constituency": row[0],
                "region": None,
                "electorate": number(row[columns["Electorate"]]),
                "cast": number(row[columns["Total Votes"]]),
                "rejected": rejected,
                "valid": number(row[columns["Total Votes"]]) - rejected,
            }
        )
    book = fastexcel.read_excel(str(raw.cached(SOURCE, EMB_2026).path))
    for name in book.sheet_names[1:]:
        rows = [
            [None if cell is None or str(cell).strip() == "" else str(cell).strip() for cell in row]
            for row in book.load_sheet_by_name(name, header_row=None).to_polars().iter_rows()
        ]
        region = rows[2][1].removesuffix(" Region")
        header = next(row for row in rows if row[0] is not None and row[0].startswith("Constituency"))
        columns = {
            index: label.removesuffix(" Constituency")
            for index, label in enumerate(header)
            if index > 0 and label is not None and label != "Total"
        }
        first = next(index for index, row in enumerate(rows) if row[0] is not None and row[0].startswith("Party/Ind"))
        totals = {}
        for row in rows[first + 1 :]:
            if row[0] is None:
                continue
            if row[0] in ("Total Valid Votes", "Total Rejected Papers", "Electorate"):
                totals[row[0]] = row
            elif row[0].startswith("Turnout"):
                break
            elif "Total" not in row[0] and not row[0].startswith(("Analysis", "Lack", "Voting", "Writing", "Unmarked")):
                for index, constituency in columns.items():
                    lists.append(
                        {
                            "year": 2026,
                            "region": region,
                            "constituency": constituency,
                            "party": row[0],
                            "votes": number(row[index]),
                        }
                    )
        for index, constituency in columns.items():
            units.append(
                {
                    "year": 2026,
                    "ballot": "list",
                    "constituency": constituency,
                    "region": region,
                    "electorate": number(totals["Electorate"][index]),
                    "cast": number(totals["Total Valid Votes"][index]) + number(totals["Total Rejected Papers"][index]),
                    "rejected": number(totals["Total Rejected Papers"][index]),
                    "valid": number(totals["Total Valid Votes"][index]),
                }
            )
    return units, lists, buckets


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    candidates, members = parse_pages()
    members = members.with_columns(
        party=pl.when((pl.col("year") == 2026) & (pl.col("member") == "Max Bannerman"))
        .then(pl.lit("Reform UK"))
        .otherwise(pl.col("party"))
    )
    units, lists, buckets = parse_workbooks()
    units = pl.DataFrame(units, infer_schema_length=None)
    official = {(row["year"], fold(row["constituency"]), row["bucket"]): row["votes"] for row in buckets}
    names = {
        (row["year"], fold(row["constituency"])): row["constituency"]
        for row in units.filter(pl.col("ballot") == "constituency").iter_rows(named=True)
    }
    rows = []
    for row in candidates.iter_rows(named=True):
        key = (row["year"], fold(row["constituency"]))
        bucket = BUCKETS.get((row["party"] or "").lower())
        total = official.get((*key, bucket)) if bucket is not None and key in names else None
        row["page_votes"] = row["votes"]
        row["workbook_name"] = names.get(key)
        row["checked"] = False
        row["excluded"] = total == 0 and bucket == "ind" and (row["votes"] or 0) > 0
        if total is not None and bucket != "ind":
            rivals = [
                other
                for other in candidates.filter(
                    (pl.col("year") == row["year"]) & (pl.col("constituency") == row["constituency"])
                ).iter_rows(named=True)
                if BUCKETS.get((other["party"] or "").lower()) == bucket
            ]
            if len(rivals) == 1:
                row["checked"] = True
                row["votes"] = total
        row["party_from_workbook"] = None
        if row["party"] is None and row["votes"] is not None and key in names:
            matches = [
                bucket for bucket in ("con", "lab", "ld", "snp", "grn") if official.get((*key, bucket)) == row["votes"]
            ]
            if len(matches) == 1:
                row["party_from_workbook"] = PARTY_LABELS[matches[0]]
                row["party"] = PARTY_LABELS[matches[0]]
        rows.append(row)
    candidates = pl.DataFrame(rows, infer_schema_length=None)
    candidates.write_parquet(PARSED / "candidates.parquet")
    members.write_parquet(PARSED / "members.parquet")
    units.write_parquet(PARSED / "units.parquet")
    pl.DataFrame(lists, infer_schema_length=None).write_parquet(PARSED / "list_votes.parquet")
    for year in sorted(set(candidates["year"])):
        winners = candidates.filter((pl.col("year") == year) & pl.col("elected"))
        held = candidates.filter(pl.col("year") == year)
        print(
            year,
            "constituencies",
            held["constituency"].n_unique(),
            "matched",
            held.filter(pl.col("workbook_name").is_not_null())["constituency"].n_unique(),
            "winners",
            len(winners),
            "list members",
            len(members.filter(pl.col("year") == year)),
            "changed",
            len(held.filter(pl.col("checked") & (pl.col("votes") != pl.col("page_votes")))),
        )


def region_key(text: str) -> str:
    key = fold(text)
    return "lothian" if key == "lothians" else key


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet").with_columns(
        ckey=pl.col("constituency").map_elements(fold, return_dtype=pl.String)
    )
    members = pl.read_parquet(PARSED / "members.parquet").with_columns(
        rkey=pl.col("region").map_elements(region_key, return_dtype=pl.String)
    )
    units = pl.read_parquet(PARSED / "units.parquet").with_columns(
        ckey=pl.col("constituency").map_elements(fold, return_dtype=pl.String),
        rkey=pl.col("region").map_elements(region_key, return_dtype=pl.String),
    )
    list_votes = pl.read_parquet(PARSED / "list_votes.parquet").with_columns(
        ckey=pl.col("constituency").map_elements(fold, return_dtype=pl.String),
        rkey=pl.col("region").map_elements(region_key, return_dtype=pl.String),
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish Parliament elections 1999–2026 by constituency and region, from the Scottish Parliament, the Electoral Commission, and the Electoral Management Board",
            polity="GB-SCT",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        scotland = cs.polity("GB-SCT")
        gb = cs.polity("GB")
        country = cs.area("gb.country", "SCT")
        if country in cs.minted:
            raise ValueError("publish gb.umit first: the countries must exist")
        for code, rank, noun in (("gb.spr", 1, "electoral region"), ("gb.spc", 2, "constituency")):
            if conn.execute("select 1 from scheme where code = %s", [code]).fetchone() is None:
                cs.add("scheme", code=code, polity_id=scotland, kind="electoral_district", rank=rank, noun=noun)
        fptp = cs.key("gb.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("publish gb.parliament first: first past the post must exist")
        ams = cs.key("gb.electoral_system", "scottish-ams-list")
        if ams in cs.minted:
            cs.add("electoral_system", id=ams, polity_id=scotland, rules={"formula": "dhondt"}, rules_version=1)
            name(ams, "Additional member system, regional lists (Scottish Parliament)")
            cs.add("note", entity_id=ams, kind="methodology", body_md=AMS)
        body = cs.key("gb.body", "scottish-parliament")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=scotland, kind="unicameral")
            name(body, "Scottish Parliament")
        cs.add("body_size", body_id=body, seats=129, valid_from=ELECTIONS[1999])

        sources: dict[str, UUID] = {}
        for key, publisher, title, url, licence, licence_url, attribution in SOURCES:
            sources[key] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[key],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=licence,
                license_url=licence_url,
                attribution=attribution,
            )
        for url, key in (
            *((url, "page") for url in PAGES.values()),
            (EC_2007, "ec2007"),
            (ANALYSIS_2011, "analysis2011"),
            (EC_2011, "ec2011"),
            (EC_2016, "ec2016"),
            (EMB_2021, "emb2021"),
            (EMB_2026, "emb2026"),
            (EMB_2026_FULL, "emb2026full"),
        ):
            cs.artifact(raw.cached(SOURCE, url), sources[key])

        parties: dict[str, UUID] = {}

        def party_of(label: str | None) -> UUID | None:
            if label is None or label in umit.NOT_PARTIES or label == "Ind":
                return None
            key = PARTY_KEYS.get(label.lower()) or umit.ALIASES.get(label) or slug(label)
            if key not in parties:
                if key in umit.EXISTING:
                    parties[key] = cs.key(*umit.EXISTING[key])
                else:
                    parties[key] = cs.key("gb.party", key)
                    if parties[key] in cs.minted:
                        cs.add("party", id=parties[key], kind="party", polity_id=gb)
                        name(parties[key], umit.NAMES.get(key, label))
            return parties[key]

        duplicate = conn.execute(
            "select entity_id from external_id where scheme = 'gb.party' and value = 'edinburgh-east-lothian-people'"
        ).fetchone()
        if duplicate is not None:
            cs.merge(duplicate[0], party_of("Edinburgh and East Lothian People"))
        for name_id, entity, text in conn.execute(
            "select id, entity_id, text from name where text = %s", ("Scottish Women'S Equality Party",)
        ).fetchall():
            cs.add(
                "name",
                entity_id=entity,
                text="Scottish Women's Equality Party",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add(
                "name_drop", name_id=name_id, entity_id=entity, text=text, reason="Title-casing broke the possessive."
            )
        for old, label in (
            ("natural-law-party", "Natural Law Party"),
            ("pro-life-alliance", "Pro-Life Alliance"),
            ("humanist-party", "Humanist Party"),
        ):
            held = conn.execute(
                "select entity_id from external_id where scheme = 'gb.party' and value = %s", (old,)
            ).fetchone()
            if held is not None and held[0] != party_of(label):
                cs.merge(held[0], party_of(label))

        starts = sorted(set(ERAS.values()))
        ends = dict(itertools.pairwise(starts))
        region_areas: dict[tuple[int, str], UUID] = {}
        region_units: dict[tuple[int, str], UUID] = {}
        constituency_areas: dict[tuple[int, str], UUID] = {}
        constituency_units: dict[tuple[int, str], UUID] = {}
        region_labels: dict[tuple[int, str], str] = {}
        located = {
            (2011, row["ckey"]): row["rkey"]
            for row in units.filter((pl.col("ballot") == "list") & (pl.col("year") == 2011)).iter_rows(named=True)
        }
        for constituency, region in regions_1999().items():
            for part in ("Orkney", "Shetland") if constituency == "Orkney and Shetland" else (constituency,):
                located[(1999, fold(BOUNDARY_NAMES.get(part, part)))] = region_key(BOUNDARY_NAMES.get(region, region))
        for constituency, region in conn.execute(
            """select cn.text, rn.text from area c
               join area_code cc on cc.area_id = c.id and cc.code like '2026:%'
               join name cn on cn.entity_id = c.id and cn.is_preferred
               join area_geometry cg on cg.area_id = c.id and cg.valid @> date '2026-05-07'
               join area r on r.scheme_code = 'gb.spr'
               join area_geometry rg on rg.area_id = r.id and rg.valid @> date '2026-05-07'
                 and st_covers(rg.geom, st_pointonsurface(cg.geom))
               join name rn on rn.entity_id = r.id and rn.is_preferred
               where c.scheme_code = 'gb.spc'"""
        ).fetchall():
            located[(2026, fold(constituency))] = region_key(region)
        for era in starts:
            start = ELECTIONS[era]
            end = ELECTIONS[ends[era]] if era in ends else None
            first = candidates.filter(pl.col("year") == era)
            for row in members.filter(pl.col("year") == era).iter_rows(named=True):
                region_labels.setdefault(
                    (era, row["rkey"]), "Lothian" if row["rkey"] == "lothian" and era == 2011 else row["region"]
                )
            for (rkey,), label in ((key[1:], label) for key, label in region_labels.items() if key[0] == era):
                code = f"{era}:{slug(label)}"
                area = region_areas[(era, rkey)] = cs.area("gb.spr", code, start)
                unit = region_units[(era, rkey)] = cs.unit("gb.spr", code)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="gb.spr")
                    cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
                    cs.add("area_parent", area_id=area, parent_id=country, valid_from=start, valid_to=end)
                    name(area, label)
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=scotland,
                        scheme_code="gb.spr",
                        code=code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=start,
                        valid_to=end,
                    )
                    name(unit, label)
            for ckey, label in first.select("ckey", "constituency").unique().iter_rows():
                code = f"{era}:{slug(label)}"
                parent = region_areas[(era, located[(era, ckey)])]
                area = constituency_areas[(era, ckey)] = cs.area("gb.spc", code, start)
                unit = constituency_units[(era, ckey)] = cs.unit("gb.spc", code)
                cs.add("area_parent", area_id=area, parent_id=parent, valid_from=start, valid_to=end)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="gb.spc")
                    cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
                    name(area, label)
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=scotland,
                        scheme_code="gb.spc",
                        code=code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=start,
                        valid_to=end,
                    )
                    name(unit, label)

        elected_names = {
            slug(text) for text in (*candidates.filter(pl.col("elected"))["candidate"], *members["member"])
        }
        persons: dict[str, UUID] = {}

        def person_of(year: int, place: str, text: str) -> UUID:
            identity = slug(text) if slug(text) in elected_names else f"{year}:{slug(place)}:{slug(text)}"
            if identity not in persons:
                persons[identity] = cs.key(f"{SOURCE}.person", identity)
                if persons[identity] in cs.minted:
                    cs.add("person", id=persons[identity])
                    name(persons[identity], text)
            return persons[identity]

        def value(result_set: UUID, unit: UUID, option: UUID | None, measure: int, amount: int | None) -> None:
            if amount is None:
                return
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=amount,
            )

        for year, day in ELECTIONS.items():
            era = ERAS[year]
            election = cs.key("gb.election", str(day))
            cs.add(
                "election",
                id=election,
                polity_id=scotland,
                kind="general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{year} Scottish Parliament election")
            path = f"gb/scotland/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            workbook = {2007: "ec2007", 2011: "ec2011", 2016: "ec2016", 2021: "emb2021", 2026: "emb2026full"}.get(year)
            held = candidates.filter(pl.col("year") == year)
            year_units = units.filter(pl.col("year") == year)
            constituency_figures = {
                row["ckey"]: row for row in year_units.filter(pl.col("ballot") == "constituency").iter_rows(named=True)
            }
            for (constituency,), lines in held.group_by("constituency", maintain_order=True):
                ckey = fold(constituency)
                unit = constituency_units[(era, ckey)]
                contest_key = f"{day}:{slug(constituency)}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=constituency_areas[(era, ckey)],
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Scottish Parliament election in {constituency}")
                cs.add("slug", path=f"{path}/{slug(constituency)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                figures = constituency_figures.get(ckey)
                shown = lines.filter(~pl.col("excluded"))
                missing = shown.filter(pl.col("votes").is_null())
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources["page"],
                    is_default=True,
                    completeness=0.5 if len(missing) else 1,
                )
                if len(missing):
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="The Scottish Parliament's page prints no vote count for "
                        + " and ".join(missing["candidate"])
                        + " in this contest. Their votes are left blank rather than inferred.",
                    )
                total = shown["votes"].drop_nulls().sum()
                if figures is not None and not len(missing) and total != figures["valid"]:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=f"The candidates' votes on the Scottish Parliament's page sum to {total:,}, but the "
                        f"{'Electoral Management Board' if year == 2021 else 'Electoral Commission'} reports "
                        f"{figures['valid']:,} valid votes. The page's figures are kept for candidates the "
                        "official workbook does not break out.",
                    )
                if figures is not None:
                    for measure, amount in (
                        (REGISTERED, figures["electorate"]),
                        (BALLOTS, figures["cast"]),
                        (VALID, figures["valid"]),
                        (INVALID, figures["rejected"]),
                    ):
                        value(result_set, unit, None, measure, amount)
                        if amount is not None:
                            cs.add(
                                "value_locator",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=None,
                                channel=0,
                                measure=measure,
                                count_round=0,
                                source_id=sources[workbook],
                            )
                for line in lines.iter_rows(named=True):
                    if line["excluded"]:
                        cs.add(
                            "note",
                            entity_id=contest,
                            kind="context",
                            body_md=f"The Scottish Parliament's page lists {line['candidate']} "
                            f"({line['party'] or 'no party'}) with {line['page_votes']:,} votes. The Electoral "
                            "Commission's workbook records no candidate in that column and a valid total that "
                            "excludes them, so they are not shown.",
                        )
                        continue
                    option_key = f"{contest_key}:{slug(line['candidate'])}:{slug(line['party'] or 'none')}"
                    option = cs.key("gb.option", option_key)
                    party = party_of(line["party"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=line["candidate"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.candidacy", option_key),
                        person_id=person_of(year, f"{constituency}:{line['party']}", line["candidate"]),
                        option_id=option,
                        role="head",
                        ballot_name=line["candidate"],
                        member_of_party_id=party,
                        is_independent=line["party"] is None or line["party"] in umit.NOT_PARTIES,
                    )
                    if line["party_from_workbook"] is not None:
                        cs.add(
                            "note",
                            entity_id=contest,
                            kind="context",
                            body_md=f"The Scottish Parliament's page prints no party for {line['candidate']}. The "
                            f"workbook's {line['party_from_workbook']} total for this constituency equals their "
                            f"{line['votes']:,} votes, so that party is shown.",
                        )
                    value(result_set, unit, option, VOTES, line["votes"])
                    if line["checked"] and line["votes"] is not None:
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            source_id=sources[workbook],
                        )
                        if line["votes"] != line["page_votes"]:
                            cs.add(
                                "note",
                                entity_id=contest,
                                kind="context",
                                body_md=f"The Scottish Parliament's page prints {line['page_votes']:,} votes for "
                                f"{line['candidate']}; the Electoral Commission's workbook gives {line['votes']:,} "
                                "for their party, which is used here.",
                            )
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")

            list_source = {
                1999: "analysis2011",
                2003: "analysis2011",
                2007: "analysis2011",
                2011: "ec2011",
                2016: "ec2016",
                2021: "emb2021",
                2026: "emb2026",
            }.get(year)
            year_lists = list_votes.filter(
                (pl.col("year") == year) & ~((pl.col("year") == 2021) & (pl.col("votes") == 0))
            )
            expansions = {2011: LISTS_2011, 2016: LISTS_2016}.get(year, {})
            for (rkey,), region_members in members.filter(pl.col("year") == year).group_by("rkey", maintain_order=True):
                label = region_labels[(era, rkey)]
                contest_key = f"{day}:list:{slug(label)}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=region_areas[(era, rkey)],
                    magnitude=len(region_members),
                    round_number=1,
                    electoral_system_id=ams,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Scottish Parliament election, {label} regional list")
                cs.add("slug", path=f"{path}/{slug(label)}-regional-list", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[list_source or "page"],
                    is_default=True,
                    completeness=1 if list_source is not None else 0,
                )
                if list_source is None:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=NO_LIST_VOTES)
                else:
                    for note in conn.execute(
                        "select id from note where entity_id = %s and body_md = %s", [result_set, NO_LIST_VOTES]
                    ).fetchall():
                        cs.add(
                            "note_drop",
                            note_id=note[0],
                            entity_id=result_set,
                            body_md=NO_LIST_VOTES,
                            reason="the regional list votes by party are now loaded",
                        )
                if year in (1999, 2003, 2007):
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=HISTORY_LISTS)
                    for note, text in conn.execute(
                        "select id, body_md from note where entity_id = %s and body_md like %s and body_md <> %s",
                        [result_set, "The regional list votes are%", HISTORY_LISTS],
                    ).fetchall():
                        cs.add(
                            "note_drop",
                            note_id=note,
                            entity_id=result_set,
                            body_md=text,
                            reason="replaced by the note on the official figures, which supersede the earlier lists",
                        )
                region_lists = year_lists.filter(pl.col("rkey") == rkey)
                lothian_2011 = year == 2011 and rkey == "lothian"
                options: dict[str, UUID] = {}
                by_party: dict[UUID, UUID] = {}
                for source_label in dict.fromkeys(region_lists["party"]):
                    individual = (
                        re.fullmatch(r"[A-Z' -]+, .+", source_label) is not None
                        and " - " not in source_label.split(",")[0]
                    )
                    other = source_label in ("Others", "Ind", "IND")
                    independent = source_label.startswith(
                        ("Independent", "Margo", "MP for", "MSP for")
                    ) and not source_label.upper().startswith("INDEPENDENT GREEN")
                    if individual:
                        surname, forename = source_label.split(", ", 1)
                        full = f"{forename} {surname.title()}"
                    elif source_label.startswith(("MP for", "MSP for")):
                        full = "Independent"
                    elif year in (2021, 2026):
                        full = (
                            source_label.title()
                            .replace("(Snp)", "(SNP)")
                            .replace("(Ukip)", "(UKIP)")
                            .replace("Uk Independence", "UK Independence")
                            .replace("Reform Uk", "Reform UK")
                            .replace("Advance Uk", "Advance UK")
                            .replace("’S", "’s")
                            .replace("'S", "'s")
                        )
                    else:
                        full = expansions.get(source_label, source_label)
                    option = options[source_label] = cs.key("gb.option", f"{contest_key}:{slug(source_label)}")
                    party = None if other or individual or independent else party_of(full)
                    if party is not None:
                        by_party[party] = option
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if other else "candidate" if individual or independent else "list",
                        party_id=party,
                        ballot_label=(
                            "Other independent candidates"
                            if lothian_2011 and source_label == "Ind"
                            else "Independent candidates"
                            if source_label in ("Ind", "IND")
                            else full
                        ),
                    )
                if lothian_2011:
                    options["Margo MacDonald"] = cs.key("gb.option", f"{contest_key}:margo-macdonald")
                    cs.add(
                        "contest_option",
                        id=options["Margo MacDonald"],
                        contest_id=contest,
                        kind="candidate",
                        party_id=None,
                        ballot_label="Margo MacDonald",
                    )
                elected: dict[UUID, list[dict[str, object]]] = {}
                for line in region_members.iter_rows(named=True):
                    party = party_of(line["party"])
                    if party is None:
                        option = options.get(line["member"])
                        if option is None:
                            option = cs.key("gb.option", f"{contest_key}:{slug(line['member'])}")
                            cs.add(
                                "contest_option",
                                id=option,
                                contest_id=contest,
                                kind="candidate",
                                party_id=None,
                                ballot_label=line["member"],
                            )
                    elif party in by_party:
                        option = by_party[party]
                    elif list_source is None:
                        option = by_party[party] = cs.key("gb.option", f"{contest_key}:{slug(line['party'])}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="list",
                            party_id=party,
                            ballot_label=line["party"],
                        )
                    else:
                        raise ValueError(f"{year} {label}: no list for {line['member']} ({line['party']})")
                    if year == 2026 and line["member"] == "Max Bannerman":
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md="The Scottish Parliament's page prints no party for Max Bannerman. He is shown "
                            "on Reform UK's list, the one that the D'Hondt allocation gives a second seat in "
                            "Highlands and Islands.",
                        )
                    elected.setdefault(option, []).append({**line, "party_id": party})
                for option, winners in elected.items():
                    listed = winners[0]["party_id"] is not None
                    if listed:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=len(winners))
                    else:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                    for line in winners:
                        candidacy = cs.key("gb.candidacy", f"{contest_key}:{slug(line['member'])}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person_of(year, label, line["member"]),
                            option_id=option,
                            role="list_member" if listed else "head",
                            ballot_name=line["member"],
                            member_of_party_id=line["party_id"],
                            is_independent=not listed,
                        )
                        if listed:
                            cs.add(
                                "outcome",
                                result_set_id=result_set,
                                option_id=option,
                                candidacy_id=candidacy,
                                kind="elected",
                            )
                kept = {row["id"] for row in cs.rows["contest_option"] if row["contest_id"] == contest}
                for (stale,) in conn.execute(
                    "select id from contest_option where contest_id = %s", [contest]
                ).fetchall():
                    if stale not in kept:
                        cs.add(
                            "option_drop",
                            option_id=stale,
                            contest_id=contest,
                            reason=DROP_REASONS.get(year, "no longer a ballot option of this contest in the emit"),
                        )
                if list_source is None:
                    continue
                list_units = year_units.filter((pl.col("ballot") == "list") & (pl.col("rkey") == rkey))
                for row in list_units.iter_rows(named=True):
                    unit = (
                        region_units[(era, rkey)]
                        if year in (1999, 2003, 2007, 2021)
                        else constituency_units[(era, row["ckey"])]
                    )
                    for measure, amount in (
                        (REGISTERED, row["electorate"]),
                        (BALLOTS, row["cast"]),
                        (VALID, row["valid"]),
                        (INVALID, row["rejected"]),
                    ):
                        value(result_set, unit, None, measure, amount)
                for row in region_lists.iter_rows(named=True):
                    if lothian_2011 and row["party"] == "Ind":
                        continue
                    unit = (
                        region_units[(era, rkey)]
                        if year in (1999, 2003, 2007, 2021)
                        else constituency_units[(era, row["ckey"])]
                    )
                    value(result_set, unit, options[row["party"]], VOTES, row["votes"])
                if lothian_2011:
                    pooled = region_lists.filter(pl.col("party") == "Ind")["votes"].sum()
                    margo = next(
                        int(row[3])
                        for row in sheet(raw.cached(SOURCE, ANALYSIS_2011).path, "Table 3")
                        if row[0] == "Margo MacDonald"
                    )
                    value(result_set, region_units[(era, rkey)], options["Margo MacDonald"], VOTES, margo)
                    value(result_set, region_units[(era, rkey)], options["Ind"], VOTES, pooled - margo)
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=f"The Electoral Commission's workbook pools every independent in Lothian into one column "
                        f"by constituency ({pooled:,} votes). The Scottish Parliament's election analysis gives Margo "
                        f"MacDonald {margo:,} regionally, so her votes and the other independents' ({pooled - margo:,}) "
                        "are recorded once, at the region, and not by constituency.",
                    )
        renamed = {
            region_key("Central Scotland and Lothians West"): region_key("Central Scotland"),
            region_key("Edinburgh and Lothians East"): region_key("Lothian"),
        }
        for before, after in itertools.pairwise(starts):
            for (era, rkey), area in region_areas.items():
                if era != after:
                    continue
                event = uuid5(NAMESPACE, f"area_event\x1fgb.spr\x1f{after}\x1f{rkey}")
                cs.add(
                    "area_event",
                    id=event,
                    kind="boundary_changed",
                    date=ELECTIONS[after],
                    notes=f"The {region_labels[(after, rkey)]} region was redefined for the {after} election.",
                )
                cs.add(
                    "area_event_member",
                    event_id=event,
                    area_id=region_areas[(before, renamed.get(rkey, rkey))],
                    role="from",
                )
                cs.add("area_event_member", event_id=event, area_id=area, role="to")
        return cs.write()
