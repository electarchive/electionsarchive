import html
import re
import subprocess
import time
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import httpx
import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.referendums"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
COUNTRIES = {"E": "ENG", "W": "WLS", "S": "SCT", "N": "NIR"}
EC_PUBLISHER = "The Electoral Commission"
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
EU_2016 = (
    "https://web.archive.org/web/20231220033756if_/"
    "https://www.electoralcommission.org.uk/sites/default/files/2019-07/EU-referendum-result-data.csv"
)
SIR_2014 = (
    "https://www.electoralcommission.org.uk/sites/default/files/pdf_file/Scottish-independence-referendum-report.pdf"
)
SCOTTISH_NAMES = {
    "Comhairle Nan": "Eilean Siar",
    "Dumfries &": "Dumfries and Galloway",
    "East": "East Dunbartonshire",
    "West": "West Dunbartonshire",
    "Orkney": "Orkney Islands",
    "Shetland": "Shetland Islands",
    "Dundee": "Dundee City",
    "Glasgow": "Glasgow City",
}
RP97 = (
    "https://web.archive.org/web/2024if_/https://researchbriefings.files.parliament.uk/documents/RP97-113/RP97-113.pdf"
)
RP97_PAGE = "https://commonslibrary.parliament.uk/research-briefings/rp97-113/"
LIBRARY = "House of Commons Library"
ALIASES = {
    "clackmannan": "clackmannanshire",
    "cityofaberdeen": "aberdeencity",
    "cityofdundee": "dundeecity",
    "cityofglasgow": "glasgowcity",
    "perthshireandkinross": "perthandkinross",
    "borders": "scottishborders",
    "orkneyislands": "orkneyislands",
    "westernisles": "eilean siar".replace(" ", ""),
    "rhonddacynontaff": "rhonddacynontaf",
    "cityofwestminster": "westminster",
}
SENEDD = "https://senedd.wales/media/gclkwa5f/11-017-english.pdf"
SENEDD_PAGE = "https://research.senedd.wales/research-articles/the-2011-referendum-on-the-national-assembly-for-wales/"
GLA98 = "https://data.london.gov.uk/download/24r9z/6559d43c-4ea0-4b58-b705-94f84c3a549a/LBCE_1998-5-7.pdf"
GLA98_PAGE = "https://data.london.gov.uk/dataset/london-borough-council-elections-1998-24r9z"
AV_SITE = "http://ukreferendumresults.aboutmyvote.co.uk/en/"
AV_SNAPSHOT = "https://web.archive.org/web/20110511001911if_/"
AV_LISTS = tuple(
    f"https://web.archive.org/web/20110509155352if_/{AV_SITE}all-local-voting-area-declarations.aspx?ft={n}"
    for n in range(1, 9)
)
RP1144 = (
    "https://web.archive.org/web/2024if_/https://researchbriefings.files.parliament.uk/documents/RP11-44/RP11-44.pdf"
)
AV_REGIONS = r"East Midlands|Eastern|London|North East|North West|Northern Ireland|Scotland|South East|South West|Wales|West Midlands|Yorkshire and the Humber"
ELECTIONS = {
    "1998-05-07": "1998 Greater London Authority referendum",
    "2011-03-03": "2011 Welsh Assembly powers referendum",
    "1979-03-01": "1979 Scottish and Welsh devolution referendums",
    "1997-09-11": "1997 Scottish devolution referendum",
}
RP97_NOTE = (
    "From House of Commons Library Research Paper 97/113 (Richard Dewdney, 10 November 1997). Its 1997 figures are "
    "from the Scottish Office and Welsh Office and its 1979 figures from F. W. S. Craig's British Electoral Facts; it "
    "gives each area's votes counted for Yes and No, but electorates and spoilt ballots only for the whole country."
)
REFERENDUMS = {
    "1998-05-07": {
        "title": "Greater London Authority",
        "question": (
            "Are you in favour of the Government's proposals for a Greater London Authority, made up of an elected "
            "Mayor and a separately elected Assembly?"
        ),
        "answers": ("Yes", "No"),
        "source": (
            "London Borough Council Elections May 1998, including the Greater London Authority referendum results",
            GLA98,
        ),
        "publisher": "London Research Centre",
        "page": GLA98_PAGE,
        "license": "Published by the London Research Centre; the Greater London Authority lists it on the London Datastore with no licence stated",
        "license_url": None,
        "attribution": "London Research Centre, London Borough Council Elections May 1998, pp. 224-226.",
        "note": (
            "Counted by the 32 London boroughs and the City of London. The report's table (read from a scan) gives "
            "each area's electorate, Yes votes, and No votes; its borough figures sum to the London totals it prints "
            "(1,230,759 Yes and 478,413 No). Its ballot paper and rejected paper columns do not reconcile with those "
            "counts, so only electorates and Yes and No votes are stored."
        ),
    },
    "2011-03-03": {
        "title": "National Assembly for Wales law-making powers",
        "question": (
            "Do you want the Assembly now to be able to make laws on all matters in the 20 subject areas it has "
            "powers for, without needing to ask the UK Parliament for its agreement?"
        ),
        "answers": ("Yes", "No"),
        "source": ("The 2011 Referendum on the National Assembly for Wales, Quick Guide 11/017", SENEDD),
        "publisher": "National Assembly for Wales Commission",
        "page": SENEDD_PAGE,
        "license": "National Assembly for Wales Commission copyright; free reproduction with acknowledgment",
        "license_url": None,
        "attribution": "National Assembly for Wales Commission, The 2011 Referendum on the National Assembly for Wales (11/017), from Electoral Commission figures.",
        "note": (
            "Counted by the 22 unitary authorities of Wales. The National Assembly for Wales Commission's research "
            "paper tabulates the Electoral Commission's results, giving each authority's Yes and No votes and "
            "turnout; electorates and rejected ballots are not given by authority."
        ),
    },
    "1979-03-01:scotland": {
        "title": "Scotland Act 1978",
        "question": "Do you want the provisions of the Scotland Act 1978 to be put into effect?",
        "answers": ("Yes", "No"),
        "source": ("Results of Devolution Referendums (1979 & 1997), Research Paper 97/113", RP97),
        "publisher": LIBRARY,
        "license": "House of Commons Library research paper; the paper states no licence",
        "license_url": None,
        "attribution": "House of Commons Library, Research Paper 97/113, Results of Devolution Referendums (1979 & 1997).",
        "page": RP97_PAGE,
        "outcome": "lost_yes",
        "note": RP97_NOTE
        + " The Act required Yes votes from 40 percent of the electorate; Yes won 51.6 percent of the votes cast but "
        "32.5 percent of the electorate (32.9 percent on the adjusted register), so the Act was not put into effect. "
        "3,133 ballots were spoilt. Counted by the 12 regions and island areas of Scotland.",
    },
    "1979-03-01:wales": {
        "title": "Wales Act 1978",
        "question": "Do you want the provisions of the Wales Act 1978 to be put into effect?",
        "answers": ("Yes", "No"),
        "source": ("Results of Devolution Referendums (1979 & 1997), Research Paper 97/113", RP97),
        "publisher": LIBRARY,
        "license": "House of Commons Library research paper; the paper states no licence",
        "license_url": None,
        "attribution": "House of Commons Library, Research Paper 97/113, Results of Devolution Referendums (1979 & 1997).",
        "page": RP97_PAGE,
        "note": RP97_NOTE
        + " Wales rejected the Act, with Yes at 20.3 percent of the votes cast. 3,309 ballots were spoilt. Counted by "
        "the eight counties of Wales.",
    },
    "1997-09-11:parliament": {
        "title": "Scottish Parliament",
        "question": "I agree that there should be a Scottish Parliament.",
        "answers": (
            "I agree that there should be a Scottish Parliament",
            "I do not agree that there should be a Scottish Parliament",
        ),
        "source": ("Results of Devolution Referendums (1979 & 1997), Research Paper 97/113", RP97),
        "publisher": LIBRARY,
        "license": "House of Commons Library research paper; the paper states no licence",
        "license_url": None,
        "attribution": "House of Commons Library, Research Paper 97/113, Results of Devolution Referendums (1979 & 1997).",
        "page": RP97_PAGE,
        "note": RP97_NOTE
        + " 11,986 ballots were spoilt on this question. Counted by the 32 council areas of Scotland.",
    },
    "1997-09-11:tax": {
        "title": "Tax-varying powers",
        "question": "I agree that a Scottish Parliament should have tax-varying powers.",
        "answers": (
            "I agree that a Scottish Parliament should have tax-varying powers",
            "I do not agree that a Scottish Parliament should have tax-varying powers",
        ),
        "source": ("Results of Devolution Referendums (1979 & 1997), Research Paper 97/113", RP97),
        "publisher": LIBRARY,
        "license": "House of Commons Library research paper; the paper states no licence",
        "license_url": None,
        "attribution": "House of Commons Library, Research Paper 97/113, Results of Devolution Referendums (1979 & 1997).",
        "page": RP97_PAGE,
        "note": RP97_NOTE
        + " 19,013 ballots were spoilt on this question. Counted by the 32 council areas of Scotland.",
    },
    "1997-09-18": {
        "name": "1997 Welsh devolution referendum",
        "title": "Welsh Assembly",
        "question": "I agree that there should be a Welsh Assembly.",
        "answers": (
            "I agree that there should be a Welsh Assembly",
            "I do not agree that there should be a Welsh Assembly",
        ),
        "source": ("Results of Devolution Referendums (1979 & 1997), Research Paper 97/113", RP97),
        "publisher": LIBRARY,
        "license": "House of Commons Library research paper; the paper states no licence",
        "license_url": None,
        "attribution": "House of Commons Library, Research Paper 97/113, Results of Devolution Referendums (1979 & 1997).",
        "page": RP97_PAGE,
        "note": RP97_NOTE + " 3,999 ballots were spoilt. Counted by the 22 unitary authorities of Wales.",
    },
    "2011-05-05": {
        "name": "2011 United Kingdom Alternative Vote referendum",
        "title": "Parliamentary voting system",
        "question": (
            "At present, the UK uses the \u201cfirst past the post\u201d system to elect MPs to the House of Commons. "
            "Should the \u201calternative vote\u201d system be used instead?"
        ),
        "answers": ("Yes", "No"),
        "source": ("UK referendum results 2011 (archived results site)", AV_LISTS[0]),
        "artifacts": (*AV_LISTS[1:], RP1144),
        "page": "https://www.electoralcommission.org.uk/our-work/our-research/past-elections-and-referendums",
        "note": (
            "Counted by 440 voting areas: the local authorities of England, the Scottish Parliament and Senedd "
            "constituencies, and Northern Ireland as one area. Taken from the Electoral Commission's results website "
            "as it stood in May 2011, via the Internet Archive, with electorates from House of Commons Library Research Paper "
            "11/44, which took them from the Commission. Rejected ballots (113,292 in all) are not given by area."
        ),
    },
    "2014-09-18": {
        "name": "2014 Scottish independence referendum",
        "title": "Scottish independence",
        "question": "Should Scotland be an independent country?",
        "answers": ("Yes", "No"),
        "source": ("Report on the Scottish independence referendum held on 18 September 2014", SIR_2014),
        "page": (
            "https://www.electoralcommission.org.uk/research-reports-and-data/"
            "our-reports-and-data-past-elections-and-referendums/report-scottish-independence-referendum"
        ),
        "note": (
            "Counted by the 32 council areas of Scotland. The Commission's report gives each council's votes counted, "
            "Yes, No, and rejected papers; it gives the electorate (4,283,938) only for Scotland as a whole."
        ),
    },
    "2016-06-23": {
        "name": "2016 United Kingdom European Union membership referendum",
        "title": "European Union membership",
        "question": "Should the United Kingdom remain a member of the European Union or leave the European Union?",
        "answers": ("Remain a member of the European Union", "Leave the European Union"),
        "source": ("Results and turnout at the EU referendum", EU_2016),
        "page": (
            "https://www.electoralcommission.org.uk/research-reports-and-data/"
            "our-reports-and-data-past-elections-and-referendums/results-and-turnout-eu-referendum"
        ),
        "note": (
            "Counted by 382 areas: the local authorities of Great Britain, Northern Ireland as one area, and Gibraltar. "
            "The result was advisory."
        ),
    },
}


def fetch() -> None:
    raw.fetch(SOURCE, EU_2016, suffix=".csv")
    raw.fetch(SOURCE, SIR_2014, suffix=".pdf")
    raw.fetch(SOURCE, RP97, suffix=".pdf")
    raw.fetch(SOURCE, SENEDD, suffix=".pdf")
    raw.fetch(SOURCE, GLA98, suffix=".pdf")
    raw.fetch(SOURCE, RP1144, suffix=".pdf")
    for url in AV_LISTS:
        raw.fetch(SOURCE, url, suffix=".html")
        time.sleep(1)


def parse() -> None:
    frames = []
    eu = pl.read_csv(raw.cached(SOURCE, EU_2016).path, infer_schema_length=0, encoding="utf8-lossy")
    frames.append(
        eu.select(
            referendum=pl.lit("2016-06-23"),
            code=pl.col("Area_Code"),
            name=pl.col("Area"),
            region=pl.col("Region"),
            electorate=pl.col("Electorate").cast(pl.Int64),
            cast=pl.col("Votes_Cast").cast(pl.Int64),
            valid=pl.col("Valid_Votes").cast(pl.Int64),
            rejected=pl.col("Rejected_Ballots").cast(pl.Int64),
            yes=pl.col("Remain").cast(pl.Int64),
            no=pl.col("Leave").cast(pl.Int64),
        )
    )
    codes = dict(zip(eu["Area"], eu["Area_Code"], strict=True))
    folded = {re.sub(r"[^a-z]", "", name.lower().replace("&", "and")): code for name, code in codes.items()}
    england = []
    electorates = {}
    for line in subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, RP1144).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines():
        match = re.match(
            r"^\s*(?:\d+\s+)?(\D+?)\s{2,}(?:Yes|No)\s+([\d,]+)\s+([\d,]+)\s+[\d,]+\s+[\d.]+%\s+[\d.]+%\s+([\d,]+)\s+[\d.]+%\s*$",
            line,
        )
        if match is not None:
            electorates[(int(match[2].replace(",", "")), int(match[3].replace(",", "")))] = int(
                match[4].replace(",", "")
            )
    for url in AV_LISTS:
        text = re.sub(
            r"[\s|]*\|[\s|]*",
            "|",
            html.unescape(
                re.sub(r"<[^>]+>", "|", raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace"))
            ),
        )
        for label, region, yes, no in re.findall(rf"\|([^|]+)\|({AV_REGIONS})\|([\d,]+)\|([\d,]+)(?=\|)", text):
            label = html.unescape(label)
            yes, no = int(yes.replace(",", "")), int(no.replace(",", ""))
            code = (
                "N92000002"
                if label == "Northern Ireland"
                else codes.get(label) or folded.get(re.sub(r"[^a-z]", "", label.lower().replace("&", "and")))
            )
            england.append(
                {
                    "referendum": "2011-05-05",
                    "code": code or f"AV2011-{slug(label)}",
                    "name": label,
                    "region": region,
                    "electorate": electorates.get((yes, no)),
                    "cast": yes + no,
                    "valid": yes + no,
                    "rejected": None,
                    "yes": yes,
                    "no": no,
                }
            )
    if len(england) != 440 or sum(r["yes"] for r in england) != 6152607 or sum(r["no"] for r in england) != 13013123:
        raise ValueError(f"{len(england)} voting areas parsed")
    if sum(r["electorate"] is None for r in england):
        print("no electorate:", [r["name"] for r in england if r["electorate"] is None])
    if sum(r["electorate"] or 0 for r in england) != 45684501:
        print("electorate sum", sum(r["electorate"] or 0 for r in england), "against 45,684,501")
    frames.append(pl.DataFrame(england, schema=frames[0].schema))
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, SIR_2014).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    scotland = []
    for line in text[text.index("Summary of referendum result") :].splitlines():
        match = re.match(r"^(\D+?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%$", line)
        if match is None or match[1] == "Total":
            continue
        label = SCOTTISH_NAMES.get(match[1], match[1].replace("&", "and"))
        counted, yes, no, rejected = (int(match[n].replace(",", "")) for n in range(2, 6))
        scotland.append(
            {
                "referendum": "2014-09-18",
                "code": codes[label],
                "name": label,
                "region": None,
                "electorate": None,
                "cast": counted,
                "valid": yes + no,
                "rejected": rejected,
                "yes": yes,
                "no": no,
            }
        )
    if len(scotland) != 32:
        raise ValueError(f"{len(scotland)} Scottish councils parsed")
    frames.append(pl.DataFrame(scotland, schema=frames[0].schema))
    library = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, RP97).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    council = {re.sub(r"[^a-z]", "", name.lower()): code for name, code in codes.items()}
    for table, country, current, keys in (
        ("Table 1", "SCT", False, ["1979-03-01:scotland"]),
        ("Table 2", "SCT", True, ["1997-09-11:parliament", "1997-09-11:tax"]),
        ("Table 5", "WLS", False, ["1979-03-01:wales"]),
        ("Table 6", "WLS", True, ["1997-09-18"]),
    ):
        start = library.index(table) + 1
        end = next(n for n in range(start, len(library)) if "ranked by" in library[n])
        found = 0
        for line in library[start:end]:
            match = re.match(r"^(\D+?)\s{2,}(\d[\d,.\s]*)$", line.rstrip())
            if match is None or match[1].startswith(("adjusted", "Scotland", "Wales", "Turnout")):
                continue
            numbers = [float(n.replace(",", "")) for n in match[2].split()]
            if len(numbers) != (14 if table == "Table 2" else 8):
                continue
            found += 1
            label = match[1].strip()
            folded_label = re.sub(r"[^a-z]", "", label.lower())
            if current:
                code = council[ALIASES.get(folded_label, folded_label)]
            else:
                code = f"{country.lower()}-{'region' if country == 'SCT' else 'county'}-1974-{slug(label)}"
            for key, (yes, no) in zip(
                keys,
                [(numbers[2], numbers[5]), *([(numbers[8], numbers[11])] if len(numbers) > 8 else [])],
                strict=False,
            ):
                if key != "1997-09-11:tax" and yes + no != numbers[1] and label != "Fife":
                    raise ValueError(f"{key} {label}: counts do not add up")
                frames.append(
                    pl.DataFrame(
                        {
                            "referendum": key,
                            "code": code,
                            "name": label,
                            "region": None,
                            "electorate": None,
                            "cast": int(yes + no),
                            "valid": int(yes + no),
                            "rejected": None,
                            "yes": int(yes),
                            "no": int(no),
                            "country": country,
                        },
                        schema={**frames[0].schema, "country": pl.String},
                    )
                )
        if found != {"Table 1": 12, "Table 2": 32, "Table 5": 8, "Table 6": 22}[table]:
            raise ValueError(f"{table}: {found} areas parsed")
    senedd = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, SENEDD).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    start = next(n for n, line in enumerate(senedd) if "Turnout" in line and "Yes votes" in line)
    welsh = []
    for line in senedd[start + 1 : start + 26]:
        match = re.match(r"^\s+(\D+?)\s{2,}[\d.]+\s+[\d.]+\s+([\d,]+)\s+[\d.]+\s+([\d,]+)$", line)
        if match is None or match[1] == "Wales":
            continue
        label = match[1].replace("The ", "")
        folded_label = re.sub(r"[^a-z]", "", label.lower())
        yes, no = int(match[2].replace(",", "")), int(match[3].replace(",", ""))
        welsh.append(
            {
                "referendum": "2011-03-03",
                "code": council[ALIASES.get(folded_label, folded_label)],
                "name": label,
                "region": None,
                "electorate": None,
                "cast": yes + no,
                "valid": yes + no,
                "rejected": None,
                "yes": yes,
                "no": no,
                "country": "WLS",
            }
        )
    if len(welsh) != 22 or sum(r["yes"] for r in welsh) != 517132 or sum(r["no"] for r in welsh) != 297380:
        raise ValueError("Welsh 2011 table does not match its published totals")
    frames.append(pl.DataFrame(welsh, schema={**frames[0].schema, "country": pl.String}))
    gla = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, GLA98).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    start = next(n for n, line in enumerate(gla) if "City of London" in line and "5,075" in line)
    london = []
    for line in gla[start : start + 90]:
        match = re.match(
            r"^\D*?\s*([A-Z][A-Za-z ]+?)\s{2,}([\d,]{5,})\s+[\d.]+\s+([\d,]+)\s+[\d.]+\s+[\d.]+\s+([\d,]+)\s+[\d,]+\s+[\d,]+\s*$",
            line,
        )
        if match is None:
            continue
        label = re.sub(r"^[A-Za-z]\s+", "", match[1].strip())
        if label == "London":
            break
        folded_label = re.sub(r"[^a-z]", "", label.lower())
        electorate, yes, no = (int(match[n].replace(",", "")) for n in (2, 3, 4))
        london.append(
            {
                "referendum": "1998-05-07",
                "code": council[ALIASES.get(folded_label, folded_label)],
                "name": label,
                "region": None,
                "electorate": electorate,
                "cast": yes + no,
                "valid": yes + no,
                "rejected": None,
                "yes": yes,
                "no": no,
                "country": "ENG",
            }
        )
    if len(london) != 33 or sum(r["yes"] for r in london) != 1230759 or sum(r["no"] for r in london) != 478413:
        raise ValueError(f"London 1998: {len(london)} areas, {sum(r['yes'] for r in london)} Yes")
    frames.append(pl.DataFrame(london, schema={**frames[0].schema, "country": pl.String}))
    rows = pl.concat(frames, how="diagonal").with_columns(
        country=pl.coalesce(pl.col("country"), pl.col("code").str.slice(0, 1).replace_strict(COUNTRIES, default=None)),
    )
    bad = rows.filter(
        (pl.col("yes") + pl.col("no") != pl.col("valid"))
        | (pl.col("valid") + pl.col("rejected").fill_null(0) != pl.col("cast"))
    )
    if bad.height:
        raise ValueError(f"counts do not add up: {bad}")
    PARSED.mkdir(parents=True, exist_ok=True)
    rows.write_parquet(PARSED / "results.parquet")
    print(
        rows.group_by("referendum").agg(pl.len(), pl.col("yes").sum(), pl.col("no").sum(), pl.col("electorate").sum())
    )


def emit() -> Path:
    rows = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="United Kingdom referendums by counting area, from the Electoral Commission and the counting officers",
            polity="GB",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = 'gb.counting_area'").fetchone() is None:
            cs.add("scheme", code="gb.counting_area", polity_id=gb, kind="administrative", rank=2, noun="counting area")
        countries = {code: cs.area("gb.country", code) for code in COUNTRIES.values()}
        nation = cs.area("gb.nation", "GBR")
        if cs.minted & {*countries.values(), nation}:
            raise ValueError("publish gb.umit first: the countries must exist")
        system = cs.key("gb.electoral_system", "referendum")
        if system in cs.minted:
            cs.add("electoral_system", id=system, polity_id=gb, rules={"formula": "referendum"}, rules_version=1)
            name(system, "United Kingdom referendum (simple majority)")

        units: dict[str, UUID] = {}
        for code, label, country in (
            rows.sort("referendum")
            .group_by("code", maintain_order=True)
            .agg(pl.col("name").last(), pl.col("country").last())
            .iter_rows()
        ):
            units[code] = cs.unit("gb.counting_area", code)
            if units[code] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=gb,
                    scheme_code="gb.counting_area",
                    code=code,
                    area_relation="unlocated" if country is None else "within",
                    area_id=None if country is None else countries[country],
                    kind="standard",
                    valid_from=None,
                )
                name(units[code], label)

        elections: dict[str, UUID] = {}
        written: set[UUID] = set()
        for key, held in rows.group_by("referendum", maintain_order=True):
            (key,) = key
            day = key[:10]
            meta = REFERENDUMS[key]
            when = date.fromisoformat(day)
            publisher, url = meta["source"]
            owner = meta.get("publisher", EC_PUBLISHER)
            source = cs.source(owner, publisher)
            if source not in written:
                written.add(source)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=owner,
                    title=publisher,
                    url=meta["page"],
                    license=meta.get("license", "Open Government Licence v3.0"),
                    license_url=meta.get("license_url", OGL),
                    attribution=meta.get(
                        "attribution",
                        f"Contains information from {owner} licensed under the Open Government Licence v3.0.",
                    ),
                    notes=meta["note"],
                )
            for artifact_url in (url, *meta.get("artifacts", ())):
                cs.artifact(raw.cached(SOURCE, artifact_url), source)
            election = elections.get(day)
            path = f"gb/{day}-referendum"
            if election is None:
                election = elections[day] = cs.key("gb.election", f"{day}:referendums" if day == "2011-05-05" else day)
                cs.add(
                    "election",
                    id=election,
                    polity_id=gb,
                    kind="referendum",
                    date_from=when,
                    date_to=when,
                    status="held",
                )
                name(election, ELECTIONS.get(day) or meta["name"])
                cs.add("slug", path=path, entity_id=election)
            contest = cs.key("gb.contest", f"{key}:referendum")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="plebiscite")
            name(contest, meta["title"])
            cs.add("slug", path=f"{path}/{slug(meta['title'])}", entity_id=contest)
            cs.add(
                "ballot_question_text",
                id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1fen"),
                contest_id=contest,
                kind="text",
                lang="en",
                body=meta["question"],
                url=meta["page"],
                source_id=source,
                locator=None,
            )
            cs.add("note", entity_id=contest, kind="context", body_md=meta["note"])
            options = []
            for position, label in enumerate(meta["answers"]):
                option = cs.key("gb.option", f"{key}:{position}")
                options.append(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=label,
                )
            result_set = cs.key(f"{SOURCE}.result_set", key)
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
                completeness=1,
            )
            totals = held.select(pl.col("yes").sum(), pl.col("no").sum()).row(0)
            if meta.get("outcome") == "lost_yes":
                cs.add("outcome", result_set_id=result_set, option_id=options[0], kind="lost")
            else:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[0 if totals[0] > totals[1] else 1],
                    kind="won",
                )
            for line in held.iter_rows(named=True):
                for option, measure, value in (
                    (None, REGISTERED, line["electorate"]),
                    (None, BALLOTS, line["cast"]),
                    (None, VALID, line["valid"]),
                    (None, INVALID, line["rejected"]),
                    (options[0], VOTES, line["yes"]),
                    (options[1], VOTES, line["no"]),
                ):
                    if value is None:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[line["code"]],
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
