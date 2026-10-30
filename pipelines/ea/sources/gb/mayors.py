import html
import re
import struct
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import dc_local, leap, leh
from ea.store import DATA, connect

SOURCE = "gb.mayors"
VERSION = "4"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
COUNCILS = {
    "bedford": "E06000055",
    "copeland": "E07000029",
    "croydon": "E09000008",
    "doncaster": "E08000017",
    "hackney": "E09000012",
    "leicester": "E06000016",
    "lewisham": "E09000023",
    "mansfield": "E07000174",
    "middlesbrough": "E06000002",
    "newham": "E09000025",
    "north-tyneside": "E08000022",
    "salford": "E08000006",
    "tower-hamlets": "E09000030",
    "watford": "E07000103",
}
LEAP = "https://www.andrewteale.me.uk/leap/ward/{}/"
LEAP_PAGES = {
    23657: "E07000002",
    23656: "E06000055",
    24574: "E06000023",
    23963: "E07000029",
    25319: "E09000008",
    21579: "E08000017",
    21605: "E09000012",
    24970: "E06000001",
    21854: "E06000016",
    24926: "E09000023",
    24682: "E08000012",
    22184: "E07000174",
    23655: "E06000002",
    21606: "E09000025",
    24858: "E08000022",
    24683: "E08000006",
    23964: "E06000027",
    21624: "E09000030",
    21625: "E07000103",
}
LEAP_PARTIES = {
    **leap.NATIONAL,
    "Ind": None,
    "CPA": "Christian Peoples Alliance",
    "NF": "National Front",
    "SocAlt": "Socialist Alternative",
    "RfmUK": "Reform UK",
    "Yorks": "Yorkshire Party",
    "Comm": "Communist Party of Britain",
    "Chr": "Christian Party",
    "Aspire": "Aspire",
    "THF": "Tower Hamlets First",
    "Brist1st": "Bristol First",
    "MIF": "Mansfield Independent Forum",
    "WEP": "Women's Equality Party",
    "Workers": "Workers Party of Britain",
    "AnimalW": "Animal Welfare Party",
    "SYS": "Save Your Services",
    "CG": "Community Group",
    "HPBP": "Hackney People Before Profit",
    "OneLove": "One Love Party",
    "PHacknyF": "Putting Hackney First",
    "Hckny1st": "Hackney First",
    "DVP": "Democrats and Veterans Party",
    "CUP": "Communities United Party",
    "KMCWP": "Kamran Malik Communities Welfare Party",
    "PATH": "People's Alliance of Tower Hamlets",
    "RFAC": "Red Flag Anti-Corruption",
    "FatCat": "Fat Cat Party",
    "UPS": "Unity for Peace and Socialism",
    "Bedford": "Better Bedford Independent",
    "Torbay": "Voice 4 Torbay",
    "Birthday": "The Birthday Party",
    "LivInd": "Liverpool Independent Party",
    "CA": "Community Action Party",
    "LPBP": "Lewisham People Before Profit",
    "LPA": "Lewisham Peoples Alliance",
    "LEAP": "Local Education Action by Parents",
    "PHF": "Putting Hartlepool First",
    "TTI": "Taking the Initiative Party",
}
LEAP_CANDIDATE = re.compile(
    r'<th class="cand(elected)?">(.*?)</th>\s*<td class="party(?:elected)?">(.*?)</td>\s*'
    r'<td class="votes(?:elected)?">(\d+)</td>'
)
LEAP_TITLE = "Local Elections Archive Project: {}"
LEAP_NOTE = (
    "Compiled by Andrew Teale for the Local Elections Archive Project from each council's Returning Officer: first "
    "preferences for every candidate and, where no one won a majority, the two finalists' totals after second "
    "preferences. Party codes are expanded from the candidates' registered descriptions."
)
BY_ELECTION = re.compile(r"Resignation|Death|Disqualification")
SV_FROM = date(2000, 5, 4)
SV_TO = date(2023, 5, 4)
TOWER_HAMLETS = "https://democracy.towerhamlets.gov.uk/mgElectionAreaResults.aspx?ID=126&RPID=13851705"
MANSFIELD = "https://www.mansfield.gov.uk/downloads/file/662/mayoral-results-2019"
BEDFORD = "https://www.bedford.gov.uk/files/mayoral.pdf/download?inline="
Box = tuple[float, float, float, float]
ROUND2: dict[str, tuple[str, str, str, str, dict[str, tuple[int, int | None]], int | None]] = {
    "mayor.tower-hamlets.2018-05-03": (
        TOWER_HAMLETS,
        ".html",
        "Tower Hamlets Council",
        "Election results for Tower Hamlets, 3 May 2018: Election of a Mayor of Tower Hamlets",
        {
            "John Biggs": (37619, 44865),
            "Rabina Khan": (13113, 16878),
            "Abul Monsur Ohid Ahmed": (11109, None),
            "Anwara Ali": (6149, None),
            "Elaine Bagshaw": (5598, None),
            "Ciaran Jebb": (3365, None),
            "Hugo Pierre": (728, None),
        },
        None,
    ),
    "mayor.mansfield.2019-05-02": (
        MANSFIELD,
        ".pdf",
        "Mansfield District Council",
        "Declaration of Result, Mayoral election for the District of Mansfield, 2 May 2019",
        {
            "Andy Abrahams": (6881, 7930),
            "Kate Allsop": (5860, 7928),
            "Stephen Garner": (4827, None),
            "George Jabbour": (3592, None),
            "Philip Shields": (2422, None),
        },
        546,
    ),
    "mayor.bedford.2019-05-02": (
        BEDFORD,
        ".pdf",
        "Bedford Borough Council",
        "Statement as to First and Second Preference Votes, Mayoral Election, 2 May 2019",
        {
            "Dave Hodgson": (17596, 21416),
            "Gianni Carofano": (15778, 18105),
            "Adrian Haynes": (2627, None),
            "Jenni Jackson": (9677, None),
            "Adrian Spurrell": (3239, None),
        },
        1001,
    ),
}
ROWS = {
    "Andy Abrahams": (0.181, 0.211),
    "Kate Allsop": (0.211, 0.242),
    "Stephen Garner": (0.242, 0.2725),
    "George Jabbour": (0.2725, 0.3025),
    "Philip Shields": (0.3025, 0.328),
}
COLUMNS = {1: (0.503, 0.649), 2: (0.796, 0.947)}
SCANNED: dict[str, dict[tuple[str | None, int], Box]] = {
    "mayor.mansfield.2019-05-02": {
        **{
            (person, number): (left, top, right, bottom)
            for person, (top, bottom) in ROWS.items()
            for number, (left, right) in COLUMNS.items()
        },
        (None, 0): (0.796, 0.730, 0.944, 0.7575),
    }
}
SCAN_NOTE = (
    "The declaration is a scanned form with handwritten figures; they were transcribed by reading the page image, "
    "and each value's box marks its cell on the form."
)
VOTES, INVALID, VOTERS = 1, 5, 14
SHARE_ONLY = 4
MISSING = (
    "No count of second preferences was found in the council's own publications, so only the first round is given."
)
REFERENDUMS = Path(__file__).with_name("mayors_referendums.tsv")
BRIEFINGS = {
    "sn05000": (
        (
            "https://web.archive.org/web/20260106140017if_/"
            "https://researchbriefings.files.parliament.uk/documents/SN05000/SN05000.pdf"
        ),
        "https://commonslibrary.parliament.uk/research-briefings/sn05000/",
        "Directly-elected mayors, Research Briefing SN05000 (6 May 2025)",
        "Appendix 3: Mayoral referendum results",
        (
            "Every local referendum in England and Wales on a council's executive arrangements, 2001 to 2025, with "
            "votes for and against and turnout, from Rallings and Thrasher's British Electoral Facts and council "
            "websites."
        ),
    ),
    "cbp9228": (
        (
            "https://web.archive.org/web/20260822122509if_/"
            "https://researchbriefings.files.parliament.uk/documents/CBP-9228/CBP-9228.pdf"
        ),
        "https://commonslibrary.parliament.uk/research-briefings/cbp-9228/",
        "May 2021 local elections, Research Briefing CBP-9228 (20 May 2021)",
        "5. Local referendums",
        (
            "The three governance referendums of 6 May 2021 (Newham, Sheffield, and Tower Hamlets), with spoilt "
            "ballots, from the councils' results."
        ),
    ),
    "rp05093": (
        (
            "https://web.archive.org/web/20130530214648id_/"
            "http://www.parliament.uk/documents/commons/lib/research/rp2005/rp05-093.pdf"
        ),
        "https://commonslibrary.parliament.uk/research-briefings/rp05-93/",
        "Local and Mayoral elections 2005, Research Paper 05/93 (31 December 2005)",
        "E. Mayoral elections",
        (
            "The local and mayoral elections of 5 May 2005, with first and second votes in the four mayoral "
            "elections, from David Cowling (BBC Political Research) and Rallings and Thrasher."
        ),
    ),
}
RELEASES = {
    "stoke2002": (
        (
            "https://web.archive.org/web/20021031140949id_/"
            "http://www.stoke.gov.uk:80/content/cc/news_releases/mayoral_final_results.jsp"
        ),
        (
            "https://web.archive.org/web/20021031140949/"
            "http://www.stoke.gov.uk:80/content/cc/news_releases/mayoral_final_results.jsp"
        ),
        "Stoke-on-Trent City Council",
        "17/10 Mayoral Election Final Results, press release 161_02 (October 2002)",
    ),
}
TABLES: dict[tuple[str, str, date], dict[str, tuple[str | None, int, int | None]]] = {
    ("stoke2002", "E06000021", date(2002, 10, 17)): {
        "Mike Wolfe": ("Mayor 4 Stoke", 9356, 12693),
        "George Stevenson": ("Labour Party", 9752, 12379),
        "Steven Batkin": ("British National Party", 8213, None),
        "Roger Ibbs": ("Conservative", 4417, None),
        "Geoffrey Snow": (None, 3975, None),
        "Fred Morrow": ("Liberal Democrats", 2408, None),
        "Paul Breeze": (None, 1349, None),
        "Patricia Whitehouse": (None, 1280, None),
        "Graham Wilkes": (None, 1157, None),
        "David Chatton": (None, 926, None),
        "Adrian Knapper": ("Adrian Knapper Independent Party", 708, None),
        "Harry Chesters": (None, 453, None),
    },
    ("rp05093", "E06000021", date(2005, 5, 5)): {
        "Mark Meredith": ("Labour Party", 27253, 36961),
        "Roger Ibbs": ("Conservative", 16211, 23130),
        "Mike Wolfe": ("Supporting Green Shoots", 15882, None),
        "Steven Batkin": ("British National Party", 15776, None),
        "Gary Chevin": (None, 4505, None),
        "Justine Harvey": (None, 1955, None),
        "Gary Falconer": (None, 1368, None),
    },
}
QUESTIONS = {
    "mayor": ("Directly elected mayor", ("Yes", "No"), "A Yes vote was for a directly elected mayor."),
    "mayor_choice": (
        "Directly elected mayor",
        ("Mayor and cabinet", "Leader and cabinet"),
        "The ballot offered a directly elected mayor or a leader chosen by the councillors.",
    ),
    "leader": (
        "Leader and cabinet",
        ("Yes", "No"),
        "A Yes vote was for a leader and cabinet in place of the directly elected mayor.",
    ),
    "retain_leader": (
        "Keeping the directly elected mayor",
        ("Keep directly elected mayor", "Change to leader and cabinet model"),
        "The ballot asked whether to keep the directly elected mayor or change to a leader and cabinet.",
    ),
    "retain_committee": (
        "Keeping the directly elected mayor",
        ("Keep directly elected mayor", "Change to committee model"),
        "The ballot asked whether to keep the directly elected mayor or change to a committee system.",
    ),
    "committee": (
        "Committee system",
        ("Yes", "No"),
        "A Yes vote was for a committee system in place of the leader and cabinet.",
    ),
}


def lines() -> pl.DataFrame:
    frames = []
    for year in dc_local.DAYS:
        try:
            frame = dc_local.result_lines(year)
        except LookupError:
            continue
        frames.append(
            frame.filter(pl.col("election_id").str.starts_with("mayor."))
            .select(
                origin=pl.lit("dc"),
                year=pl.lit(year),
                day=pl.lit(dc_local.DAYS[year]),
                ballot="election_id",
                code=pl.col("election_id").str.split(".").list.get(1).replace_strict(COUNCILS, default=None),
                person_id="person_id",
                person_name="person_name",
                party_name="party_name",
                votes=pl.col("ballots_cast").cast(pl.Int64),
                round2=pl.lit(None, pl.Int64),
                winner=pl.col("is_winner").str.to_lowercase().is_in(["true", "t", "1"]),
                spoilt=pl.col("spoilt_ballots").cast(pl.Int64, strict=False),
                source="source",
                reason=pl.lit(None, pl.String),
            )
            .filter(pl.col("code").is_not_null())
        )
    return pl.concat(frames)


def fetch() -> None:
    for url, suffix, _, _, _, _ in ROUND2.values():
        raw.fetch(SOURCE, url, suffix=suffix)
    for url, *_ in BRIEFINGS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url, *_ in RELEASES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for page in LEAP_PAGES:
        url = LEAP.format(page)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            response = httpx.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-GB,en;q=0.9",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like "
                    "Gecko) Chrome/130.0 Safari/537.36",
                },
                timeout=60,
                follow_redirects=True,
            )
            response.raise_for_status()
            raw.store(
                SOURCE, url, response.content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html"
            )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    dc = lines()
    held = set(zip(dc["code"], dc["day"]))
    people = {(row["code"], leh.fold(row["person_name"])): row["person_id"] for row in dc.iter_rows(named=True)}
    rows = []
    for page, code in LEAP_PAGES.items():
        url = LEAP.format(page)
        text = raw.cached(SOURCE, url).path.read_text()
        for block in text.split("<h4>")[1:]:
            heading = html.unescape(re.sub("<[^>]+>", "", block.split("</h4>")[0]))
            day = datetime.strptime(
                re.sub(r"(\d+)(st|nd|rd|th)", r"\1", re.split(r" [\[(]", heading)[0]), "%d %B %Y"
            ).date()
            candidates = [
                (bool(elected), html.unescape(name).strip(), party.strip(), int(votes))
                for elected, name, party, votes in LEAP_CANDIDATE.findall(block)
            ]
            if not candidates or (code, day) in held:
                continue
            bracket = re.search(r"\[(.*)\]", heading)
            reasons = [
                part
                for part in (bracket.group(1).split("; ") if bracket else [])
                if " gain from " not in part and not part.endswith(" hold")
            ]
            finals: dict[str, int] = {}
            if "<h5>Transfers" in block:
                for name, cells in re.findall(
                    r'<th class="candtransfers">(.*?)</th>(.*?)</tr>', block.split("<h5>Transfers")[1], re.DOTALL
                ):
                    stages = re.findall(r'<td class="transfers(?:elected)?">(\d*)</td>', cells)
                    if html.unescape(name).strip() != "Stage" and len(stages) > 1 and stages[1]:
                        finals[html.unescape(name).strip()] = int(stages[1])
            unknown = {party for *_, party, _ in candidates if party not in LEAP_PARTIES}
            if unknown:
                raise ValueError(f"{url} {day}: no party for {sorted(unknown)}")
            if not any(elected for elected, *_ in candidates):
                top = sorted(candidates, key=lambda row: -row[3])
                if day < SV_TO or top[0][3] == top[1][3]:
                    raise ValueError(f"{url} {day}: no candidate is marked elected")
                candidates = [(row == top[0], *row[1:]) for row in candidates]
            for elected, name, party, votes in candidates:
                rows.append(
                    {
                        "origin": "leap",
                        "year": day.year,
                        "day": day,
                        "ballot": f"leap:{code}:{day}",
                        "code": code,
                        "person_id": people.get((code, leh.fold(name)), f"{code}:{slug(name)}"),
                        "person_name": name,
                        "party_name": LEAP_PARTIES[party],
                        "votes": votes,
                        "round2": finals.get(name),
                        "winner": elected,
                        "spoilt": None,
                        "source": url,
                        "reason": "; ".join(reasons) or None,
                    }
                )
    for (key, code, day), candidates in TABLES.items():
        path = raw.cached(SOURCE, (BRIEFINGS[key] if key in BRIEFINGS else RELEASES[key])[0]).path
        text = (
            subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True).stdout
            if path.suffix == ".pdf"
            else path.read_bytes()
        )
        printed = set(re.findall(r"\d+", text.decode(errors="replace").replace(",", "")))
        figures = {str(votes) for _, *pair in candidates.values() for votes in pair if votes is not None}
        if not figures <= printed:
            raise ValueError(f"{key} {code} {day}: {sorted(figures - printed)} not printed")
        winner = max(candidates, key=lambda name: candidates[name][2] or 0)
        for name, (party, votes, final) in candidates.items():
            rows.append(
                {
                    "origin": "library",
                    "year": day.year,
                    "day": day,
                    "ballot": f"library:{code}:{day}",
                    "code": code,
                    "person_id": people.get((code, leh.fold(name)), f"{code}:{slug(name)}"),
                    "person_name": name,
                    "party_name": party,
                    "votes": votes,
                    "round2": final,
                    "winner": name == winner,
                    "spoilt": None,
                    "source": key,
                    "reason": None,
                }
            )
    frame = pl.concat([dc, pl.DataFrame(rows, schema=dc.schema)])
    for (ballot,), group in frame.group_by("ballot"):
        if group["winner"].sum() != 1:
            raise ValueError(f"{ballot}: {group['winner'].sum()} winners")
        if group["origin"][0] == "dc":
            continue
        runoff = group.filter(pl.col("round2").is_not_null())
        (winner,) = group.filter("winner").rows(named=True)
        supplementary = SV_FROM <= group["day"][0] < SV_TO
        if runoff.height:
            if not supplementary or runoff.height != 2 or (runoff["round2"] < runoff["votes"]).any():
                raise ValueError(f"{ballot}: the second round must carry two candidates, each above their first round")
            if runoff.sort("round2")["person_name"][-1] != winner["person_name"]:
                raise ValueError(f"{ballot}: the second round's leader is not the elected candidate")
        elif supplementary and winner["votes"] * 2 <= group["votes"].sum():
            raise ValueError(f"{ballot}: no majority on first preferences and no second round")
        elif not supplementary and winner["votes"] != group["votes"].max():
            raise ValueError(f"{ballot}: the elected candidate did not top the poll")
    for ballot, (url, suffix, _, _, rounds, _) in ROUND2.items():
        group = frame.filter(pl.col("ballot") == ballot)
        if rounds.keys() != set(group["person_name"]):
            raise ValueError(f"{ballot}: the declaration and Democracy Club list different candidates")
        artifact = raw.cached(SOURCE, url)
        text = (
            subprocess.run(["pdftotext", "-layout", str(artifact.path), "-"], check=True, capture_output=True).stdout
            if suffix == ".pdf"
            else artifact.path.read_bytes()
        ).decode(errors="replace")
        printed = set(re.findall(r"\d[\d,]*", text.replace(",", "")))
        figures = {votes for pair in rounds.values() for votes in pair if votes is not None}
        if ballot not in SCANNED and not {str(votes) for votes in figures} <= printed:
            raise ValueError(f"{ballot}: {sorted(figures - {int(f) for f in printed})} not printed in the declaration")
        finals = {person: final for person, (_, final) in rounds.items() if final is not None}
        if len(finals) != 2 or any(final < rounds[person][0] for person, final in finals.items()):
            raise ValueError(f"{ballot}: the second round must carry two candidates, each above their first round")
        (winner,) = group.filter("winner")["person_name"]
        if max(finals, key=finals.__getitem__) != winner:
            raise ValueError(f"{ballot}: the second round's leader is not Democracy Club's winner")
    frame.write_parquet(PARSED / "candidates.parquet")
    print(f"PARSE-DONE {frame['ballot'].n_unique()} ballots, {frame.height} candidates")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Directly elected council mayors of England, 2002 on, from Democracy Club's results where it "
            "has the ballot and Andrew Teale's Local Elections Archive Project, the House of Commons Library, or the council otherwise, with the second round of "
            "each supplementary vote count from the council's declaration where Democracy Club gives none, and every "
            "council governance referendum since 2001 from the House of Commons Library",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        fptp = cs.key("gb.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("publish gb.parliament first")
        sv = cs.key("gb.electoral_system", "sv")
        cs.add(
            "electoral_system",
            id=sv,
            polity_id=gb,
            valid_from=SV_FROM,
            valid_to=SV_TO,
            rules={"formula": "supplementary"},
            rules_version=1,
        )
        name(sv, "Supplementary vote")
        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        party_ids: dict[str, UUID | None] = {"Independent": None}
        library: dict[str, UUID] = {}
        for key, (url, page, title, _, notes) in BRIEFINGS.items():
            library[key] = cs.source("House of Commons Library", title)
            if library[key] in cs.minted:
                cs.add(
                    "source",
                    id=library[key],
                    kind="official_body",
                    publisher="House of Commons Library",
                    title=title,
                    url=page,
                    license="Open Parliament Licence v3.0",
                    license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
                    attribution="Contains Parliamentary information licensed under the Open Parliament Licence v3.0.",
                    notes=notes,
                )
                cs.artifact(raw.cached(SOURCE, url), library[key])
        sources: dict[int | str, UUID] = {**library}
        for key, (url, page, publisher, title) in RELEASES.items():
            sources[key] = cs.source(publisher, title)
            if sources[key] in cs.minted:
                cs.add("source", id=sources[key], kind="official_body", publisher=publisher, title=title, url=page)
                cs.artifact(raw.cached(SOURCE, url), sources[key])
        offices: set[UUID] = set()
        added: set[UUID] = set()
        for (ballot,), group in frame.sort("ballot").group_by("ballot", maintain_order=True):
            first = group.row(0, named=True)
            year, day, code = first["year"], first["day"], first["code"]
            by = first["reason"] is not None and BY_ELECTION.search(first["reason"]) is not None
            election = cs.key("gb.election", f"{day}:local:{code}")
            polity = cs.key("gb.polity", code)
            area = cs.area("gb.lad", code)
            council_name, path = conn.execute(
                "select n.text, s.path from name n join slug s on s.entity_id = n.entity_id"
                " where n.entity_id = %s and n.is_preferred order by s.is_current desc limit 1",
                [polity],
            ).fetchone()
            place = council_name.removesuffix(" City Council").removesuffix(" Council")
            if election in cs.minted:
                national = conn.execute(
                    "select entity_id from external_id where scheme = 'gb.election' and value = %s",
                    [f"{day}:local:gb"],
                ).fetchone()
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=None if national is None else national[0],
                    kind="by_election" if by else "local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} {place} mayoral {'by-election' if by else 'election'}")
                cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
            if first["origin"] == "leap" and first["source"] not in sources:
                spanned = frame.filter(pl.col("source") == first["source"])["year"]
                title = LEAP_TITLE.format(f"Mayor of {place}, {spanned.min()}–{spanned.max()}")
                sources[first["source"]] = cs.source("Andrew Teale", title)
                if sources[first["source"]] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[first["source"]],
                        kind="compendium",
                        publisher="Andrew Teale",
                        title=title,
                        url=first["source"],
                        license=leap.LICENSE,
                        license_url=leap.LICENSE_URL,
                        attribution=leap.ATTRIBUTION,
                        notes=LEAP_NOTE,
                    )
                    cs.artifact(raw.cached(SOURCE, first["source"]), sources[first["source"]])
            if first["origin"] == "dc" and year not in sources:
                title = f"Candidate results, {day}"
                sources[year] = cs.source(dc_local.DC, title)
                if sources[year] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[year],
                        kind="compendium",
                        publisher=dc_local.DC,
                        title=title,
                        url=(dc_local.RESULTS if year in dc_local.ARCHIVED else dc_local.API).format(day=day),
                        license=dc_local.LICENSE,
                        license_url=dc_local.LICENSE_URL,
                        attribution=dc_local.ATTRIBUTION,
                        notes=dc_local.METHODOLOGY,
                    )
                    for url in dc_local.result_pages(year):
                        cs.artifact(raw.cached(dc_local.SOURCE, url), sources[year])
            office = cs.key("gb.office", f"mayor:{code}")
            if office not in offices:
                offices.add(office)
                cs.add("office", id=office, polity_id=polity, body_id=None, kind="mayor")
                name(office, f"Mayor of {place}")
            unit = cs.unit("gb.lad", code)
            if unit in cs.minted and unit not in added:
                added.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="gb.lad",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, place)
            supplementary = SV_FROM <= day < SV_TO
            contest_key = f"{day}:mayor:{code}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                body_id=None,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=sv if supplementary else fptp,
                default_result_kind="counted",
                term="remainder" if by else "full",
            )
            name(contest, f"Mayor of {place}, {year}")
            cs.add("slug", path=f"{path}/{day}-local/mayor", entity_id=contest)
            if first["origin"] == "dc":
                cs.add("external_id", entity_id=contest, scheme="democracyclub.ballot", value=ballot)
            if first["reason"] is not None:
                cs.add("note", entity_id=contest, kind="context", body_md=f"{first['reason']}.")
            rows = group.sort("votes", descending=True).rows(named=True)
            options: dict[str, UUID] = {}
            for row in rows:
                label = row["party_name"] or "Independent"
                if label not in party_ids:
                    found = (
                        cs.key(*leh.RESOLVED[label])
                        if label in leh.RESOLVED
                        else known.get(leh.fold(dc_local.ALIASES.get(label, label)))
                    )
                    if found is None:
                        found = cs.key("gb.party", slug(label))
                        if found in cs.minted and found not in party_ids.values():
                            cs.add("party", id=found, kind="party", polity_id=gb)
                            name(found, label)
                    party_ids[label] = found
                owner = party_ids[label]
                option_key = f"{contest_key}:{row['person_id']}"
                option = options[row["person_id"]] = cs.key("gb.mayors.option", option_key)
                person = cs.key(
                    "gb.mayors.person" if ":" in row["person_id"] else "gb.dc_local.person", row["person_id"]
                )
                if person in cs.minted and person not in added:
                    added.add(person)
                    cs.add("person", id=person)
                    name(person, row["person_name"])
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=owner,
                    ballot_label=row["person_name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.mayors.candidacy", option_key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["person_name"],
                    member_of_party_id=owner,
                    is_independent=label == "Independent",
                )
            ids = {row["person_name"]: row["person_id"] for row in rows}
            winner = next(row["person_id"] for row in rows if row["winner"])
            first_round = 1 if supplementary else 0
            dc_values = {
                row["person_id"]: {first_round: row["votes"], **({} if row["round2"] is None else {2: row["round2"]})}
                for row in rows
            }
            sets: list[
                tuple[
                    UUID,
                    UUID,
                    bool,
                    dict[str, dict[int, int]],
                    int | None,
                    dict[tuple[str | None, int], Box],
                    UUID | None,
                ]
            ] = []
            second = ROUND2.get(ballot)
            council_values: dict[str, dict[int, int]] = {}
            if second is not None:
                url, _, publisher, title, rounds, rejected = second
                council_values = {ids[person]: {1: votes, 2: final or 0} for person, (votes, final) in rounds.items()}
                council_source = cs.source(publisher, title)
                cs.add("source", id=council_source, kind="official_body", publisher=publisher, title=title, url=url)
                artifact = raw.cached(SOURCE, url)
                artifact_id = cs.artifact(artifact, council_source)
                boxes = {
                    (None if person is None else ids[person], number): box
                    for (person, number), box in SCANNED.get(ballot, {}).items()
                }
                page = None
                if boxes:
                    image = PAGES / f"{artifact.sha256}-1.png"
                    if not image.exists():
                        PAGES.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            ["pdftoppm", "-r", "100", "-png", "-f", "1", "-l", "1", "-singlefile"]
                            + [str(artifact.path), str(image.with_suffix(""))],
                            check=True,
                        )
                    width, height = struct.unpack(">II", image.read_bytes()[16:24])
                    page = cs.page(artifact_id, 1)
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifact_id,
                        page_number=1,
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
                council_set = cs.key("gb.mayors.council_result_set", contest_key)
                sets.append((council_set, council_source, True, council_values, rejected, boxes, page))
                if boxes:
                    cs.add("note", entity_id=council_set, kind="methodology", body_md=SCAN_NOTE)
                differing = [row for row in rows if council_values[row["person_id"]][1] != row["votes"]]
                for row in differing:
                    cs.add(
                        "note",
                        entity_id=council_set,
                        kind="caveat",
                        body_md=f"Democracy Club gives {row['person_name']} {row['votes']:,} votes; the declaration "
                        f"gives {council_values[row['person_id']][1]:,} first preferences.",
                    )
                if not differing:
                    sets.append(
                        (
                            cs.key("gb.mayors.result_set", contest_key),
                            sources[year],
                            False,
                            dc_values,
                            first["spoilt"],
                            {},
                            None,
                        )
                    )
            else:
                sets.append(
                    (
                        cs.key("gb.mayors.result_set", contest_key),
                        sources[year if first["origin"] == "dc" else first["source"]],
                        True,
                        dc_values,
                        first["spoilt"],
                        {},
                        None,
                    )
                )
            firsts = {person: values.get(first_round, 0) for person, values in sets[0][3].items()}
            outright = not supplementary or firsts[winner] * 2 > sum(firsts.values())
            for result_set, source, default, values, invalid, boxes, page in sets:
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                if first["origin"] == "leap":
                    cs.add("citation", entity_id=result_set, source_id=source, locator={"date": day.isoformat()})
                elif first["origin"] == "library":
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=source,
                        locator={"section": BRIEFINGS[first["source"]][3]}
                        if first["source"] in BRIEFINGS
                        else {"date": day.isoformat()},
                    )
                elif source == sources[year]:
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=source,
                        locator={"ballot_paper_id": ballot, "council_source": first["source"]},
                    )
                    if not outright and second is None:
                        cs.add("note", entity_id=result_set, kind="caveat", body_md=MISSING)
                cells: list[tuple[UUID | None, int, int, int]] = []
                if invalid is not None:
                    cells.append((None, INVALID, 0, invalid))
                for row in rows:
                    for number, votes in values[row["person_id"]].items():
                        cells.append((options[row["person_id"]], VOTES, number, votes))
                    if row["person_id"] == winner:
                        kinds = ("won", "elected")
                    elif any(2 in other for other in values.values()) and not values[row["person_id"]].get(2):
                        kinds = ("eliminated",)
                    else:
                        kinds = ("lost",)
                    for kind in kinds:
                        cs.add("outcome", result_set_id=result_set, option_id=options[row["person_id"]], kind=kind)
                by_option = {option: person for person, option in options.items()}
                for option, measure, number, votes in cells:
                    box = boxes.get((None if option is None else by_option[option], number))
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=number,
                        flags=8 if box is not None else 0,
                        value=votes,
                    )
                    if box is not None:
                        x0, y0, x1, y1 = box
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=measure,
                            count_round=number,
                            page_id=page,
                            source_id=None,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        )
        system = cs.key("gb.electoral_system", "referendum")
        if system in cs.minted:
            raise ValueError("publish gb.referendums first")
        for row in pl.read_csv(
            REFERENDUMS,
            separator="\t",
            schema_overrides={"day": pl.Date, "turnout": pl.Float64, "spoilt": pl.Int64},
        ).iter_rows(named=True):
            day, code = row["day"], row["code"]
            title, labels, meaning = QUESTIONS[row["question"]]
            held = (row["held"] or "").split()
            polity = cs.key("gb.polity", code)
            area = cs.area("gb.lad", code)
            if {polity, area} & cs.minted:
                raise ValueError(f"{code}: the council and its area must exist")
            council_name, path = conn.execute(
                "select n.text, s.path from name n join slug s on s.entity_id = n.entity_id"
                " where n.entity_id = %s and n.is_preferred order by s.is_current desc limit 1",
                [polity],
            ).fetchone()
            place = council_name.removesuffix(" City Council").removesuffix(" Council")
            election = cs.key("gb.election", f"{day}:local:{code}")
            if election in cs.minted:
                national = conn.execute(
                    "select entity_id from external_id where scheme = 'gb.election' and value = %s",
                    [f"{day}:local:gb"],
                ).fetchone()
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=None if national is None else national[0],
                    kind="referendum",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                kind = "governance" if row["question"] == "committee" else "mayoral"
                name(election, f"{day.year} {place} {kind} referendum")
                election_path = f"{path}/{day}-referendum"
                cs.add("slug", path=election_path, entity_id=election)
            else:
                (election_path,) = conn.execute(
                    "select path from slug where entity_id = %s order by is_current desc limit 1", [election]
                ).fetchone()
            unit = cs.unit("gb.lad", code)
            if unit in cs.minted and unit not in added:
                added.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="gb.lad",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, place)
            contest_key = f"{day}:referendum:{code}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add(
                "ballot_question",
                contest_id=contest,
                type="initiative"
                if "petition" in held
                else "mandatory_referendum"
                if "mandated" in held
                else "optional_referendum",
            )
            name(contest, title)
            cs.add("slug", path=f"{election_path}/{slug(title)}", entity_id=contest)
            context = [meaning]
            if "petition" in held:
                context.append("Held after a petition from local electors.")
            if "mandated" in held:
                context.append("Held by order of the government.")
            if "postal" in held:
                context.append("Voting was by post only.")
            cs.add("note", entity_id=contest, kind="context", body_md=" ".join(context))
            answers: list[UUID] = []
            for position, label in enumerate(labels):
                option = cs.key("gb.mayors.option", f"{contest_key}:{position}")
                answers.append(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=label,
                )
            result_set = cs.key("gb.mayors.result_set", contest_key)
            source = library[row["source"]]
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
            cs.add("citation", entity_id=result_set, source_id=source, locator={"section": BRIEFINGS[row["source"]][3]})
            if row["caveat"] is not None:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=row["caveat"])
            if row["first"] == row["second"]:
                raise ValueError(f"{contest_key}: tied")
            cs.add("outcome", result_set_id=result_set, option_id=answers[row["first"] < row["second"]], kind="won")
            for option, measure, flags, value in (
                (answers[0], VOTES, 0, row["first"]),
                (answers[1], VOTES, 0, row["second"]),
                (None, INVALID, 0, row["spoilt"]),
                (None, VOTERS, SHARE_ONLY, None if row["turnout"] is None else round(row["turnout"] / 100, 4)),
            ):
                if value is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=flags,
                        value=value,
                    )
        return cs.write()
