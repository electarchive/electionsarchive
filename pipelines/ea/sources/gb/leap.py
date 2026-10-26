import csv
import html
import io
import os
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import leh
from ea.store import DATA, connect

SOURCE = "gb.leap"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-073"
PAGES = DATA / "inbox" / "manual" / "MF-105"
PAGE_YEARS = (2002, 2003, 2004, 2005)
PAGE_CODES = {"Bedfordshire": "E10000001"}
SCOTLAND = {
    "Aberdeen",
    "Aberdeenshire",
    "Angus",
    "Argyll and Bute",
    "Clackmannanshire",
    "Dumfries and Galloway",
    "Dundee",
    "East Ayrshire",
    "East Dunbartonshire",
    "East Lothian",
    "East Renfrewshire",
    "Edinburgh",
    "Eilean Siar",
    "Falkirk",
    "Fife",
    "Glasgow",
    "Highland",
    "Inverclyde",
    "Midlothian",
    "Moray",
    "North Ayrshire",
    "North Lanarkshire",
    "Orkney",
    "Perth and Kinross",
    "Renfrewshire",
    "Scottish Borders",
    "Shetland",
    "South Ayrshire",
    "South Lanarkshire",
    "Stirling",
    "West Dunbartonshire",
    "West Lothian",
}
SITE = "https://www.andrewteale.me.uk"
FILES = {
    2006: ("/pdf/2006/2006_results.csv.zip", "/pdf/2006/2006-source.zip"),
    2007: ("/pdf/2007/2007-results.csv.zip", None),
    2008: ("/pdf/2008/2008_results.zip", "/pdf/2008/2008-source.zip"),
    2009: ("/pdf/2009/2009_results.zip", "/pdf/2009/2009-source.zip"),
    2010: ("/pdf/2010/2010_results.zip", "/pdf/2010/2010-source.zip"),
    2011: ("/pdf/2011/2011_results.zip", None),
    2012: ("/pdf/2012/2012_results.zip", None),
    2013: ("/pdf/2013/2013_results.zip", None),
    2014: ("/pdf/2014/2014_results.zip", None),
    2015: ("/pdf/2015/2015_results.zip", None),
    2016: ("/2016/2016-results.csv.zip", None),
    2017: ("/2017/leap-2017-05-04.zip", None),
}
CHD = "https://www.arcgis.com/sharing/rest/content/items/e0bc41722b1a4b76a6ecfff14f91cbb4/data"
CHD_PAGE = "https://www.arcgis.com/home/item.html?id=e0bc41722b1a4b76a6ecfff14f91cbb4"
ENTITIES = {"E05", "W05", "E06", "E07", "E08", "E09", "E10", "W06"}
NATIONAL = {
    "C": "Conservative",
    "Lab": "Labour Party",
    "LD": "Liberal Democrats",
    "Grn": "Green Party",
    "Green": "Green Party",
    "UKIP": "UK Independence Party",
    "PC": "Plaid Cymru",
    "BNP": "British National Party",
    "TUSC": "Trade Unionist and Socialist Coalition",
    "EDP": "English Democrats",
    "Respect": "Respect Party",
    "Lib": "Liberal Party",
    "SDP": "Social Democratic Party",
    "Veritas": "Veritas",
    "SocAll": "Socialist Alliance",
}
DROPPED = {(2006, "Daventry", "Warley")}
RESOLVED = {**leh.RESOLVED, "Social Democratic Party": ("gb.party", "social-democratic-party-1990")}
VOTES = 1
LICENSE = "CC BY-SA 3.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/3.0/"
ATTRIBUTION = "Andrew Teale, Local Elections Archive Project, CC BY-SA 3.0"
METHODOLOGY = (
    "Compiled by Andrew Teale for the Local Elections Archive Project from each council's Returning Officer, one row "
    "per candidate with votes and an elected mark. Party codes are expanded from the legends in the project's "
    "results books where they print one. Joint Labour and Co-operative candidates are filed under Labour, and "
    "candidates with no description under Independent. Wards where every candidate has 0 votes were won without a "
    "poll. Old ONS ward and council codes are mapped to GSS codes through the ONS Code History Database."
)


def fetch() -> None:
    raw.fetch(SOURCE, CHD, suffix=".zip")
    for year in PAGE_YEARS:
        url = f"{SITE}/leap/councils/{year}/"
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            file = PAGES / f"leap-results-{year}.html"
            stamp = datetime.fromtimestamp(file.stat().st_mtime, UTC)
            raw.store(SOURCE, url, file.read_bytes(), suffix=".html", retrieved_at=stamp, media_type="text/html")
    for paths in FILES.values():
        for path in paths:
            if path is None:
                continue
            url = SITE + path
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                file = INBOX / path.rsplit("/", 1)[1]
                stamp = datetime.fromtimestamp(file.stat().st_mtime, UTC)
                raw.store(
                    SOURCE, url, file.read_bytes(), suffix=".zip", retrieved_at=stamp, media_type="application/zip"
                )


def members(url: str, suffix: str) -> dict[str, bytes]:
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        return {name: archive.read(name) for name in archive.namelist() if name.endswith(suffix)}


def legends() -> dict[tuple[int, str | None, str], str]:
    found: dict[tuple[int, str | None, str], str] = {}
    for year, (_, source) in FILES.items():
        if source is None:
            continue
        for content in members(SITE + source, ".tex").values():
            council = None
            for line in content.decode("cp1252").splitlines():
                if heading := re.search(r"\\(section|chapter)\*?\{(.+?)\}", line):
                    if heading.group(1) == "section" or "Abbreviations" in heading.group(2):
                        council = "Abbreviations" if "Abbreviations" in heading.group(2) else heading.group(2)
                    continue
                if " = " not in line or line.lstrip().startswith("%"):
                    continue
                for part in re.split(r"\\\\|;", line):
                    if entry := re.match(r"\s*([^\s=]+)\s+=\s+(.+?)\s*$", part):
                        label = entry.group(2).replace("---", "–").replace("--", "–").replace("\\'e", "é")
                        label = label.replace("\\&", "&").replace("\\slash ", "/").replace("ï¿½", "–")
                        label = label.replace("{", "").replace("}", "")
                        found[year, council, entry.group(1)] = label
    return found


def parse() -> None:
    gss: dict[str, tuple[str, str]] = {}
    earliest: dict[str, tuple[date, str, str]] = {}
    latest: dict[str, tuple[date, str, str]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, CHD).path) as archive:
        reader = csv.DictReader(io.TextIOWrapper(archive.open("Equivalents.csv"), encoding="utf-8-sig"))
        for row in reader:
            if row["ENTITYCD"] not in ENTITIES or not row["GEOGCDO"] or row["OPER_DATE"] == row["TERM_DATE"]:
                continue
            start = datetime.strptime(row["OPER_DATE"], "%d/%m/%Y %H:%M:%S").date()
            if start == date(2009, 1, 1):
                if gss.get(row["GEOGCDO"], (row["GEOGCD"],))[0] != row["GEOGCD"]:
                    raise ValueError(f"{row['GEOGCDO']} has more than one GSS code on 1 January 2009")
                gss[row["GEOGCDO"]] = (row["GEOGCD"], row["GEOGNM"])
            if row["GEOGCDO"] not in earliest or start < earliest[row["GEOGCDO"]][0]:
                earliest[row["GEOGCDO"]] = (start, row["GEOGCD"], row["GEOGNM"])
            if row["ENTITYCD"] not in {"E05", "W05"} and (
                row["GEOGCDO"] not in latest or start > latest[row["GEOGCDO"]][0]
            ):
                latest[row["GEOGCDO"]] = (start, row["GEOGCD"], row["GEOGNM"])
        parents: dict[str, set[str]] = {}
        names: dict[str, str] = {}
        reader = csv.DictReader(io.TextIOWrapper(archive.open("ChangeHistory.csv"), encoding="latin-1"))
        for row in reader:
            if row["ENTITYCD"] in {"E05", "W05", "E58"}:
                parents.setdefault(row["GEOGCD"], set()).add(row["PARENTCD"])
                names[row["GEOGCD"]] = row["GEOGNM"].removesuffix(" ED")
    for old, (_, code, label) in earliest.items():
        gss.setdefault(old, (code, label))
    recoded = {gss[old][0]: (code, label) for old, (_, code, label) in latest.items() if gss[old][0] != code}
    found = legends()
    by_council: dict[tuple[str, str], dict[int, str]] = {}
    general: dict[str, set[str]] = {}
    for (year, council, code), label in found.items():
        general.setdefault(code, set()).add(label)
        if council not in (None, "Abbreviations"):
            by_council.setdefault((council, code), {})[year] = label
    rows = []
    known: dict[str, dict[int, str]] = {}
    for year, (results, _) in FILES.items():
        (content,) = members(SITE + results, ".csv").values()
        for number, line in enumerate(csv.reader(io.StringIO(content.decode("utf-8")))):
            council, la, ward, ward_code, person, party, votes, elected = (
                None if value == "NULL" else value.strip() for value in line
            )
            if (
                la is None
                or ward is None
                or la.startswith("S")
                or la[:3] in {"00Q", "00R"}
                or ward == "Elected Mayor"
                or (year, council, ward) in DROPPED
            ):
                continue
            la_code, la_name = gss.get(la, (la, council)) if not la.startswith(("E", "W")) else (la, council)
            if not la_code.startswith(("E", "W")):
                raise ValueError(f"{year} {council}: council code {la} has no GSS equivalent")
            first_code = la_code
            la_code, la_name = recoded.get(la_code, (la_code, la_name))
            if council is not None:
                known.setdefault(council, {})[year] = la_code
            ward_gss = gss.get(ward_code, (ward_code, None))[0] if ward_code else None
            if ward_code and (
                ward_code[: len(la)] != la
                if not la.startswith(("E", "W"))
                else ward_gss in parents and not parents[ward_gss] & {first_code, la_code}
            ):
                ward_code = ward_gss = None
            label = None
            if party in NATIONAL:
                label = NATIONAL[party]
            elif party and party != "Ind":
                spelled = by_council.get((council, party), {})
                label = spelled.get(year) or (spelled[max(spelled)] if spelled else None)
                if label is None and len(general.get(party, ())) == 1:
                    label = next(iter(general[party]))
            rows.append(
                {
                    "year": year,
                    "number": number,
                    "council": council,
                    "la_code": la_code,
                    "la_name": la_name,
                    "ward": ward,
                    "ward_code": ward_gss,
                    "old_code": ward_code if ward_code and ward_gss != ward_code else None,
                    "name": person or None,
                    "party": party or None,
                    "label": label,
                    "votes": int(votes or 0),
                    "elected": elected == "1",
                }
            )
    for held in PAGE_YEARS:
        text = raw.cached(SOURCE, f"{SITE}/leap/councils/{held}/").path.read_text(encoding="utf-8")
        pages = re.split(r"<!-- (https://www\.andrewteale\.me\.uk/leap/results/\S+) -->", text)[1:]
        for url, page in zip(pages[::2], pages[1::2], strict=True):
            title = re.search(r"<title>Local Elections Archive Project - \d{4} - (.+?)</title>", page)
            if title is None:
                raise ValueError(f"{url}: no title")
            council = html.unescape(title.group(1))
            if council in PAGE_CODES:
                la_code = PAGE_CODES[council]
            elif council in known:
                codes = known[council]
                choices = [code for code in codes.values() if held != 2005 or not code.startswith("E07")] or list(
                    codes.values()
                )
                la_code = min(
                    choices, key=lambda code: min(abs(seen - held) for seen, value in codes.items() if value == code)
                )
            elif council in SCOTLAND:
                continue
            else:
                raise ValueError(f"{held} {council}: no council code")
            consolidated = re.search(r'id="consol">.*?<table>(.*?)</table>', page, re.DOTALL)
            totals = {
                html.unescape(label): int(votes)
                for label, votes in re.findall(
                    r'<td class="cand">(.*?)</td>\s*<td class="votes">(\d+)</td>',
                    consolidated.group(1) if consolidated else "",
                )
            }
            wards = []
            for heading, table in re.findall(
                r'<h4 id="ward\d+"><a href="/leap/ward/\d+/">(.+?)</a></h4>\s*<table>(.*?)</table>', page, re.DOTALL
            ):
                found = re.fullmatch(r"(.+?)(?: \((\d+)\))?", html.unescape(heading).strip())
                entries = [
                    (
                        html.unescape(person).strip(),
                        html.unescape(party).strip(),
                        int(votes) if votes.strip().isdigit() else 0,
                    )
                    for person, party, votes in re.findall(
                        r'<th class="cand">([^<]*)</th>\s*<td class="party">([^<]*)</td>\s*<td class="(?:votes|unop)">([^<]*)</td>',
                        table,
                        re.DOTALL,
                    )
                ]
                if found is None:
                    raise ValueError(f"{url}: ward heading {heading!r}")
                wards.append((found.group(1), int(found.group(2) or 1), entries))
            sums: dict[str, int] = {}
            for _, _, entries in wards:
                for _, party, votes in entries:
                    sums[party] = sums.get(party, 0) + votes
            spelled = {
                party: matches[0]
                for party, total in sums.items()
                if party not in NATIONAL and party != "Ind" and total
                for matches in [[label for label, votes in totals.items() if votes == total]]
                if len(matches) == 1
            }
            for ward, seats, entries in wards:
                order = sorted(range(len(entries)), key=lambda index: -entries[index][2])
                winners = set(order[:seats]) if any(votes for _, _, votes in entries) else set(range(len(entries)))
                for index, (person, party, votes) in enumerate(entries):
                    label = None
                    if party in NATIONAL:
                        label = NATIONAL[party]
                    elif party and party != "Ind":
                        council_labels = by_council.get((council, party), {})
                        label = spelled.get(party) or (council_labels[min(council_labels)] if council_labels else None)
                        if label is None and len(general.get(party, ())) == 1:
                            label = next(iter(general[party]))
                    rows.append(
                        {
                            "year": held,
                            "number": len(rows),
                            "council": council,
                            "la_code": la_code,
                            "la_name": council,
                            "ward": ward,
                            "ward_code": None,
                            "old_code": None,
                            "name": person or None,
                            "party": party or None,
                            "label": label,
                            "votes": votes,
                            "elected": index in winners,
                        }
                    )
    frame = pl.DataFrame(rows)
    shared = (
        frame.filter(pl.col("ward_code").is_not_null())
        .group_by("year", "la_code", "ward_code")
        .agg(pl.col("ward").n_unique())
        .filter(pl.col("ward") > 1)
        .select("year", "la_code", "ward_code", shared=pl.lit(True))
    )
    frame = (
        frame.join(shared, on=["year", "la_code", "ward_code"], how="left")
        .with_columns(
            matches=pl.col("ward").map_elements(leh.fold, return_dtype=pl.Utf8)
            == pl.col("ward_code").replace_strict(names, default=None).map_elements(leh.fold, return_dtype=pl.Utf8)
        )
        .with_columns(
            ward_code=pl.when(pl.col("shared") & ~pl.col("matches").fill_null(False))
            .then(None)
            .otherwise(pl.col("ward_code"))
        )
        .drop("shared", "matches")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    for year, lines in frame.group_by("year", maintain_order=True):
        missing = lines.filter(pl.col("label").is_null() & pl.col("party").is_not_null() & (pl.col("party") != "Ind"))
        print(
            f"{year[0]}: {len(lines):,} candidacies, {lines.n_unique(['la_code', 'ward']):,} wards, "
            f"{len(missing)} with an unexpanded party code"
        )


DAYS = {
    2002: date(2002, 5, 2),
    2003: date(2003, 5, 1),
    2004: date(2004, 6, 10),
    2005: date(2005, 5, 5),
    2006: date(2006, 5, 4),
    2007: date(2007, 5, 3),
    2008: date(2008, 5, 1),
    2009: date(2009, 6, 4),
    2010: date(2010, 5, 6),
    2011: date(2011, 5, 5),
    2012: date(2012, 5, 3),
    2013: date(2013, 5, 2),
    2014: date(2014, 5, 22),
    2015: date(2015, 5, 7),
    2016: date(2016, 5, 5),
    2017: date(2017, 5, 4),
}


def emit() -> Path:
    years = [int(year) for year in os.environ["LEAP_YEARS"].split(",")]
    candidates = pl.read_parquet(PARSED / "candidates.parquet").filter(pl.col("year").is_in(years))
    sizes = (
        pl.read_parquet(PARSED / "candidates.parquet")
        .group_by("la_code", "year")
        .agg(wards=pl.col("ward").n_unique())
        .group_by("la_code")
        .agg(most=pl.col("wards").max())
    )
    most = dict(sizes.iter_rows())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"English and Welsh council elections {', '.join(map(str, years))} by ward, from Andrew Teale's "
            "Local Elections Archive Project",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        countries = {"E": cs.area("gb.country", "ENG"), "W": cs.area("gb.country", "WLS")}
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & {*countries.values(), *systems.values()}:
            raise ValueError("publish gb.umit and gb.parliament first")
        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB'"
            " and not exists (select from entity_merge m where m.entity_id = p.id) order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        party_ids: dict[str, UUID] = {}
        others = {
            contest
            for (contest,) in conn.execute(
                "select rs.contest_id from result_set rs join changeset c on c.id = rs.changeset_id "
                "where c.pipeline <> %s and rs.is_default",
                [SOURCE],
            ).fetchall()
        }

        sources: dict[int, UUID] = {}
        for year in years:
            title = f"Local Elections Archive Project: {year} results"
            sources[year] = cs.source("Andrew Teale", title)
            cs.add(
                "source",
                id=sources[year],
                kind="compendium",
                publisher="Andrew Teale",
                title=title,
                url=f"{SITE}/leap/downloads",
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
                notes=METHODOLOGY,
            )
            for url in (
                [SITE + path for path in FILES[year] if path is not None]
                if year in FILES
                else [f"{SITE}/leap/councils/{year}/"]
            ):
                cs.artifact(raw.cached(SOURCE, url), sources[year])
        chd = cs.source("Office for National Statistics", "Code History Database (June 2026) for the UK")
        cs.add(
            "source",
            id=chd,
            kind="official_body",
            publisher="Office for National Statistics",
            title="Code History Database (June 2026) for the UK",
            url=CHD_PAGE,
            license="Open Government Licence v3.0",
            license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            attribution="Source: Office for National Statistics licensed under the Open Government Licence v.3.0",
            notes="Equivalents table: the GSS code of each ward and council that held an older ONS code on "
            "1 January 2009.",
        )
        cs.artifact(raw.cached(SOURCE, CHD), chd)

        paths = {path for (path,) in conn.execute("select path from slug where path like 'gb/%%'").fetchall()}
        councils: dict[str, tuple[UUID, UUID, UUID, str, str]] = {}
        elections: set[UUID] = set()
        seen: set[UUID] = set()
        undeclared: list[str] = []
        for year in years:
            day = DAYS[year]
            national = cs.key("gb.election", f"{day}:local:gb")
            if national in cs.minted:
                cs.add(
                    "election",
                    id=national,
                    polity_id=gb,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(national, f"{year} local elections in England and Wales")
                cs.add("slug", path=f"gb/{day}-local", entity_id=national)
            held = candidates.filter(pl.col("year") == year)
            council_wards = dict(held.group_by("la_code").agg(pl.col("ward").n_unique()).iter_rows())
            nameless = set(
                held.group_by("la_code").agg(pl.col("name").is_null().all()).filter(pl.col("name"))["la_code"]
            )
            only_by = nameless | {
                code for code, wards in council_wards.items() if wards <= 3 and most[code] >= 4 * wards
            }
            for (code, ward_name), lines in held.group_by("la_code", "ward", maintain_order=True):
                lines = lines.sort("number")
                is_county = code.startswith("E10")
                if code not in councils:
                    area = cs.area("gb.lad", code)
                    polity = cs.key("gb.polity", code)
                    body = cs.key("gb.body", code)
                    label = lines["la_name"][0]
                    place = label.removesuffix(", City of").removesuffix(", County of")
                    if area in cs.minted:
                        cs.add("area", id=area, scheme_code="gb.lad")
                        cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                        cs.add(
                            "area_parent", area_id=area, parent_id=countries[code[0]], valid_from=None, valid_to=None
                        )
                        name(area, label)
                    if polity in cs.minted:
                        council_name = (
                            f"{place} County Council"
                            if is_county
                            else f"{place} City Council"
                            if label.endswith(", City of")
                            else f"{place} Council"
                        )
                        path = f"gb/{slug(place)}"
                        if path in paths:
                            path = f"{path}-{code.lower()}"
                        paths.add(path)
                        cs.add("polity", id=polity, kind="county" if is_county else "municipality", iso_code=None)
                        cs.add(
                            "polity_parent",
                            polity_id=polity,
                            parent_id=cs.polity("GB-WLS" if code[0] == "W" else "GB-ENG"),
                        )
                        cs.add("polity_territory", polity_id=polity, area_id=area)
                        name(polity, council_name)
                        cs.add("slug", path=path, entity_id=polity)
                    else:
                        path = conn.execute(
                            "select path from slug where entity_id = %s order by is_current desc limit 1", [polity]
                        ).fetchone()[0]
                        council_name = conn.execute(
                            "select text from name where entity_id = %s and is_preferred limit 1", [polity]
                        ).fetchone()[0]
                    if body in cs.minted:
                        cs.add("body", id=body, polity_id=polity, kind="council")
                        name(body, council_name)
                    councils[code] = (area, polity, body, path, council_name)
                area, polity, body, path, council_name = councils[code]
                seats = int(lines["elected"].sum())
                if seats == 0:
                    undeclared.append(f"{year} {code} {ward_name}")
                    continue
                by = code in only_by or lines["name"].is_null().all()
                election = cs.key("gb.election", f"{day}:local:{code}")
                if election not in elections:
                    elections.add(election)
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        parent_id=national,
                        kind="by_election" if code in only_by else "local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    plural = "s" if council_wards[code] > 1 else ""
                    name(election, f"{year} {council_name} {f'by-election{plural}' if code in only_by else 'election'}")
                    cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                ward_code = lines["ward_code"][0]
                old_code = lines["old_code"][0]
                display = " ".join(
                    word.lower() if index and word in leh.SMALL_WORDS else word
                    for index, word in enumerate(ward_name.split())
                )
                ward_key = ward_code or f"{code}:{slug(ward_name)}:{year}"
                ward_area = cs.area("gb.ward", ward_key)
                if ward_area not in seen:
                    seen.add(ward_area)
                    if ward_area in cs.minted:
                        cs.add("area", id=ward_area, scheme_code="gb.ward")
                        if ward_code is not None:
                            cs.add("area_code", area_id=ward_area, code=ward_code, valid_from=None, valid_to=None)
                        cs.add("area_parent", area_id=ward_area, parent_id=area, valid_from=None, valid_to=None)
                        name(ward_area, display)
                unit = cs.unit("gb.ward", ward_key)
                if unit not in seen:
                    seen.add(unit)
                    if unit in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="gb.ward",
                            code=ward_key,
                            area_relation="equals",
                            area_id=ward_area,
                            kind="standard",
                            valid_from=None,
                        )
                        name(unit, display)
                tacit = int(lines["votes"].sum()) == 0
                contest_key = f"{day}:local:{ward_key}{':by' if by else ''}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=ward_area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems["fptp" if seats == 1 else "block"],
                    default_result_kind="tacit" if tacit else "counted",
                    term="remainder" if by else None,
                )
                noun = "division" if is_county else "ward"
                name(contest, f"{display} {noun}{' by-election' if by else ''}, {council_name}, {year}")
                cs.add(
                    "slug",
                    path=f"{path}/{day}-local/{slug(display)}-{(ward_code or slug(ward_name)).lower()}"
                    f"{'-by-election' if by else ''}",
                    entity_id=contest,
                )
                result_set = cs.key("gb.leap.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=sources[year],
                    is_default=contest not in others,
                    completeness=1,
                )
                if year in PAGE_YEARS and not tacit:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="The Local Elections Archive's results pages for this year do not mark the winners; the "
                        "candidates with the most votes, as many as the seats, are shown as elected.",
                    )
                locator = {"council": lines["council"][0], "ward": ward_name}
                if old_code is not None:
                    locator["ons_code"] = old_code
                    cs.add("citation", entity_id=ward_area, source_id=chd, locator={"old_code": old_code})
                cs.add("citation", entity_id=result_set, source_id=sources[year], locator=locator)
                unnamed = 0
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    label = line["label"]
                    owner = None
                    if label is not None:
                        if label not in party_ids:
                            found = cs.key(*RESOLVED[label]) if label in RESOLVED else known.get(leh.fold(label))
                            if found is None:
                                found = cs.key("gb.party", slug(label))
                                if found in cs.minted and found not in party_ids.values():
                                    cs.add("party", id=found, kind="party", polity_id=gb)
                                    name(found, label)
                            party_ids[label] = found
                        owner = party_ids[label]
                    elif line["party"] not in (None, "Ind"):
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=f"The Local Elections Archive gives {line['name'] or 'one candidate'} the party "
                            f"code “{line['party']}”, which its results books do not spell out; no party is shown.",
                        )
                    option = cs.key("gb.leap.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=line["name"],
                    )
                    if line["name"] is not None:
                        person = cs.key("gb.leap.person", option_key)
                        cs.add("person", id=person)
                        name(person, line["name"])
                        cs.add(
                            "candidacy",
                            id=cs.key("gb.leap.candidacy", option_key),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=line["name"],
                            member_of_party_id=owner,
                            is_independent=line["party"] == "Ind",
                        )
                    else:
                        unnamed += 1
                    if not tacit:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=line["votes"],
                        )
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if unnamed:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="The Local Elections Archive gives no name for "
                        + ("one candidate." if unnamed == 1 else f"{unnamed} candidates."),
                    )
        print(f"no candidate marked elected, not loaded: {', '.join(undeclared)}")
        return cs.write()
