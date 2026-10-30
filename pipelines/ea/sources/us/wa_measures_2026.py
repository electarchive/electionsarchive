import html
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda, govmint
from ea.store import DATA, connect

SOURCE = "us.wa_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
KING = "https://info.kingcounty.gov/kcelections/Vote/contests/ballotmeasures.aspx?eid=55"
PUBLISHER = "King County Elections"
TITLE = "Ballot measures, November 3, 2026 General Election"
BROWSER = {"User-Agent": "Mozilla/5.0"}
SAMPLES = json.loads((Path(__file__).parent / "wa_samples_2026.json").read_text())
SAMPLE_COUNTY = {"Snohomish": "53061", "Kitsap": "53035", "Clark": "53011", "Pierce": "53053", "Thurston": "53067", "Skagit": "53057", "Spokane": "53063", "Whatcom": "53073"}
LOCAL = {
    "https://clark.wa.gov/elections/november-3-2026-general-special-election": "Clark/november_3_2026_general_election-sample_ballot.pdf",
    "https://www.piercecountywa.gov/328/Elections": "Pierce/Sample Ballot_15.pdf",
    "https://www.thurstoncountywa.gov/": "Thurston/Sample Ballot_2611.pdf",
    "https://www.skagitcountywa.gov/": "Skagit/2026-11-03-sample.pdf",
    "https://www.spokanecounty.gov/DocumentCenter/View/73769/November-3-2026-General-Election-Sample-Ballot-PDF": "Spokane/G126 Sample Ballot_202610021258308134.pdf",
    "https://www.whatcomcounty.us/1574": "Whatcom/11.03.26 Sample Ballot_202609231409027439.pdf",
}
MANUAL = DATA / "inbox" / "manual" / "MF-386"


def download(url: str, suffix: str = ".html") -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    if url in LOCAL:
        path = MANUAL / LOCAL[url]
        return raw.store(SOURCE, url, path.read_bytes(), suffix=".pdf", retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC), media_type="application/pdf")
    response = httpx.get(url, headers=BROWSER, follow_redirects=True, timeout=120)
    response.raise_for_status()
    return raw.store(
        SOURCE,
        url,
        response.content,
        suffix=suffix,
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", ""),
    )


def details() -> list[str]:
    listing = download(KING).path.read_text(encoding="utf-8", errors="replace")
    return sorted(
        {
            f"https://info.kingcounty.gov/kcelections/Vote/contests/ballotmeasures.aspx?lang=en-US&cid={cid}&groupname={group}&eid=55"
            for cid, group in re.findall(r"ballotmeasures\.aspx\?lang=en-US&amp;cid=(\d+)&amp;groupname=(\w+)", listing)
        }
    )


def fetch() -> None:
    download(KING)
    for url in details():
        download(url)
    for url in {sample["url"] for sample in SAMPLES}:
        download(url, ".pdf")


def parse() -> None:
    rows = []
    for url in details():
        page = download(url).path.read_text(encoding="utf-8", errors="replace")
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", page, flags=re.DOTALL)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))
        text = text[text.find("General Election ", text.find("Ballot measures v1")) :]
        for place, number, title, wording in re.findall(
            r"General Election (.+?) (Proposition No\. \d+) (.+?) (The .+?|Shall .+?|Should .+?|This .+?)\s+(?:Yes No|Explanatory statement)\b",
            text,
        ):
            rows.append({"url": url, "publisher": PUBLISHER, "place": place, "number": number, "title": title, "text": wording})
        if not any(row["url"] == url for row in rows):
            print("no wording:", url)
    rows.extend(SAMPLES)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height, "measures")


UNMINTED = {"City of Lake Forest Park": ("5337270", "Lake Forest Park")}


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        found: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        names: dict[tuple[str, str], str] = {}
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district', 'special_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district', 'us.special_district') and c.code like '53%%'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names[(scheme, text if scheme == "us.county" else ceda._core(text) if scheme != "us.place" else ceda._city(text))] = key
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/wa/%%'").fetchall())
        known = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.election' and value like %s", [f"{DAY}:WA:%"]
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington local ballot measures on the November 3, 2026 ballot, with official wording",
            polity="US-WA",
        )
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for row in frame.iter_rows(named=True):
            if row["publisher"] not in sources:
                source = sources[row["publisher"]] = cs.source(row["publisher"], TITLE)
                origin = KING if row["publisher"] == PUBLISHER else row["url"]
                cs.add("source", id=source, kind="official_body", publisher=row["publisher"], title=TITLE, url=origin)
                cs.artifact(raw.cached(SOURCE, origin), source)
        parent = cs.key("us.election", f"{DAY}:WA")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            place = row["place"]
            if place.startswith(("City of", "Town of")):
                key = names.get(("us.place", ceda._city(re.sub(r"^Town of ", "City of ", place))))
            elif "School District" in place:
                key = names.get(("us.school_district", ceda._core(re.sub(r"\s+No\.?\s*\d+$", "", place))))
            else:
                core = ceda._core(re.sub(r"\s+No\.?\s*(\d+)$", r" \1", place.replace("Fire Protection District", "Fire District")))
                key = names.get(("us.county", place)) or names.get(("us.special_district", core))
            if key is None and place in UNMINTED:
                geoid, text = UNMINTED[place]
                key = f"us.place:{geoid}"
                polity_id = cs.key("us.polity", f"place:{geoid}")
                path = f"us/wa/{slug(text)}"
                area_id = cs.area("us.place", geoid)
                if polity_id in cs.minted and slugs.get(path, polity_id) == polity_id:
                    cs.add("polity", id=polity_id, kind="municipality", iso_code=None)
                    cs.add("name", entity_id=polity_id, text=text, lang="en", kind="official", is_preferred=True)
                    cs.add("polity_parent", polity_id=polity_id, parent_id=cs.polity("US-WA"))
                    cs.add("slug", path=path, entity_id=polity_id)
                    cs.add("area", id=area_id, scheme_code="us.place")
                    cs.add("area_code", area_id=area_id, code=geoid, valid_from=None)
                    cs.add("area_parent", area_id=area_id, parent_id=cs.area("us.state", "53"))
                    cs.add("name", entity_id=area_id, text=f"{text}, WA", lang="en", kind="official", is_preferred=True)
                    cs.add("polity_territory", polity_id=polity_id, area_id=area_id)
                    cs.add(
                        "reporting_unit",
                        id=cs.unit("us.place", geoid),
                        polity_id=polity_id,
                        scheme_code="us.place",
                        code=geoid,
                        area_relation="equals",
                        area_id=area_id,
                        kind="standard",
                        valid_from=None,
                    )
                found[key] = (polity_id, area_id, text, path, geoid)
            if key is None and not place.startswith(("City of", "Town of")) and "School District" not in place:
                home = next((fips for name, fips in SAMPLE_COUNTY.items() if name.lower() in row["url"].lower()), None)
                county_key = f"us.county:{home}"
                if home is not None and county_key in found:
                    county, county_area, _, county_path, county_code = found[county_key]
                    minted = govmint.special(conn, cs, "WA", (county, county_area, county_path, county_code), place, slugs)
                    if minted is not None:
                        key = f"special:{home}:{place}"
                        found[key] = minted
            if key is None:
                print("no polity:", place, row["number"])
                continue
            kept += 1
            polity, area, name, path, code = found[key]
            election_key = f"{DAY}:WA:{code}"
            if key.startswith("us.school_district") and election_key not in known:
                election_key = f"{DAY}:WA:sd{code}"
            election = cs.key("us.election", election_key)
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add("name", entity_id=election, text=f"{DAY.year} {name} general election", lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = row["number"]
            contest_key = f"{DAY}:WA:{code}:measure:{slug(label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{slug(label)}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            slugs[contest_path] = contest
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
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add("name", entity_id=contest, text=f"{DAY.year} {name}: {label}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            cs.add("note", entity_id=contest, kind="context", body_md=row["title"])
            for position, answer in enumerate(("Yes", "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{answer.lower()}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("citation", entity_id=contest, source_id=sources[row["publisher"]])
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["text"],
                url=row["url"],
                source_id=sources[row["publisher"]],
            )
        print(f"{kept} of {frame.height} measures")
        return cs.write()
