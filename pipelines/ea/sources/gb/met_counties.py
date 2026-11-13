import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import leh
from ea.store import DATA, connect

SOURCE = "gb.met_counties"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
TITLE = "Metropolitan county council election articles, results boxes"
NOTE = (
    "Division votes as tabulated in the results boxes of the English Wikipedia's articles on each metropolitan "
    "county council election, which cite the Elections Centre's Local Elections Handbook or the regional press of "
    "the day."
)
DAYS = {1973: date(1973, 4, 12), 1977: date(1977, 5, 5), 1981: date(1981, 5, 7)}
ABOLISHED = date(1986, 4, 1)
COUNTIES = {
    "E11000001": "Greater Manchester",
    "E11000002": "Merseyside",
    "E11000003": "South Yorkshire",
    "E11000005": "West Midlands",
    "E11000006": "West Yorkshire",
    "E11000007": "Tyne and Wear",
}
SIZES = {"E11000001": 106, "E11000002": 99, "E11000007": 104, "E11000006": 88}
ARTICLES = {
    (1973, "E11000001"): "1973 Greater Manchester County Council election",
    (1973, "E11000002"): "1973 Merseyside County Council election",
    (1977, "E11000002"): "1977 Merseyside County Council election",
    (1981, "E11000002"): "1981 Merseyside County Council election",
    (1973, "E11000007"): "1973 Tyne and Wear County Council election",
    (1981, "E11000006"): "1981 West Yorkshire County Council election",
}
SET_ASIDE = "1977 Greater Manchester County Council election"
CITED = {
    "E11000001": "the Manchester Evening News and The Guardian of 13 April 1973",
    "E11000002": "Rallings and Thrasher's Local Elections Handbook and Local Election Summaries (the Elections Centre)",
    "E11000007": "the Sunderland Echo of 13 April 1973",
    "E11000006": "the Yorkshire Post",
}
METHOD = (
    "Candidates and votes by electoral division from the results boxes of the English Wikipedia's article "
    "[{title}](https://en.wikipedia.org/w/index.php?oldid={revision}), which cites {cited}. The council's own "
    "election report and the Elections Centre's Local Elections Handbook for {year} could not be retrieved: the handbook's shared copy is no longer public. A "
    "candidate is shown elected where the article marks the winner; in boxes with no mark, the candidates with the "
    "most votes take the division's seats."
)
PARTIAL = (
    "Only {seats} of the council's {size} seats are shown, in {held} divisions; the others are not in the archive."
)
COPIED = (
    " {divisions} are left out: their candidates and votes are word for word the same in the articles on the 1977 "
    "and 1981 elections, so one of the two articles copies the other."
)
NOTES = {
    (1973, "E11000002"): "The article's summary table gives Labour 53 seats and the Liberals 19, but its division "
    "boxes, shown here, elect 54 Labour and 18 Liberal councillors; the source behind it could not be checked.",
}
WIKIDATA = {
    "Labour Party (UK)": "Q9630",
    "Conservative Party (UK)": "Q9626",
    "Liberal Party (UK)": "Q622441",
    "Communist Party of Great Britain": "Q592068",
    "Communist Party (UK)": "Q592068",
    "National Front (UK)": "Q158093",
    "Ecology Party": "Q15980875",
    "Green Party (UK)": "Q15980875",
    "Social Democratic Party (UK)": "Q40052",
    "SDP-Liberal Alliance": "Q7389478",
    "British Democratic Party (1979)": "Q4969878",
    "Independent Liberal": "Q6016757",
}
LABELS = {
    "National Party (UK, 1976)": "National Party",
    "Socialist Alternative (England, Wales & Scotland)": "Socialist Alternative",
}
LOCAL = {"Save", "Socialist Alternative (England, Wales & Scotland)"}
INDEPENDENT = {"Independent (politician)", "Independent politician", "Independent"}
OUTCOMES = ("new seat win", "hold", "gain")
UNOPPOSED = {"unopposed", "uncontested"}
SEATS = {(1981, "E11000002", "Liverpool No.24 (Picton)"): 1}
VOTES, REGISTERED = 1, 2


def page() -> str:
    return API + urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content|ids",
            "rvslots": "main",
            "titles": "|".join(sorted([*ARTICLES.values(), SET_ASIDE])),
            "format": "json",
            "formatversion": 2,
        }
    )


def fetch() -> None:
    raw.fetch(SOURCE, page(), suffix=".json")


def templates(text: str) -> list[str]:
    found: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text) - 1:
        pair = text[index : index + 2]
        if pair == "{{":
            if depth == 0:
                start = index
            depth += 1
            index += 2
        elif pair == "}}" and depth:
            depth -= 1
            if depth == 0:
                found.append(text[start + 2 : index])
            index += 2
        else:
            index += 1
    return found


def params(body: str) -> tuple[str, dict[str, str]]:
    parts: list[str] = []
    depth = 0
    current = ""
    index = 0
    while index < len(body):
        pair = body[index : index + 2]
        if pair in ("{{", "[["):
            depth += 1
            current += pair
            index += 2
        elif pair in ("}}", "]]"):
            depth -= 1
            current += pair
            index += 2
        elif body[index] == "|" and depth == 0:
            parts.append(current)
            current = ""
            index += 1
        else:
            current += body[index]
            index += 1
    parts.append(current)
    named: dict[str, str] = {}
    for part in parts[1:]:
        key, equals, value = part.partition("=")
        if equals:
            named[key.strip().lower()] = value
    return parts[0].strip().lower(), named


def clean(text: str) -> str:
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    text = text.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", text).strip()


def link(text: str) -> str:
    return re.sub(r"\[\[([^|\]]*)(?:\|[^\]]*)?\]\]", r"\1", text).strip()


def figure(text: str) -> int | None:
    digits = clean(text).replace(",", "")
    return int(digits) if digits.isdigit() else None


def parse() -> None:
    found = json.loads(raw.cached(SOURCE, page()).path.read_text())["query"]["pages"]
    texts = {item["title"]: item["revisions"][0] for item in found}
    divisions: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    seated: Counter[tuple[int, str]] = Counter()
    for (year, code), title in ARTICLES.items():
        revision = texts[title]
        wikitext = revision["slots"]["main"]["content"]
        seen: set[str] = set()
        last = 0
        for match in re.finditer(
            r"\{\{\s*Election box begin(.*?)\{\{\s*Election box end\s*\}\}", wikitext, re.DOTALL | re.IGNORECASE
        ):
            parts = templates("{{" + match.group(1))
            _, head = params(parts[0])
            headings = re.findall(r"^===+\s*(.+?)\s*=+\s*$", wikitext[last : match.start()], re.MULTILINE)
            sections = re.findall(r"^==([^=].*?)==\s*$", wikitext[: match.start()], re.MULTILINE)
            last = match.end()
            if sections and "casual vacanc" in sections[-1].lower():
                continue
            division = clean(headings[-1] if headings else head.get("title", ""))
            if not division or division in seen:
                raise ValueError(f"{title}: box title {division!r} is blank or repeated")
            seen.add(division)
            candidates: list[dict] = []
            outcomes = 0
            electorate = None
            for part in parts[1:]:
                kind, values = params(part)
                kind = re.sub(r"\s+", " ", kind.removeprefix("election box").strip())
                if kind.startswith(("candidate", "winning candidate")):
                    party = values.get("party", "")
                    unopposed = "unopposed" in kind or clean(values.get("votes", "")).lower() in UNOPPOSED
                    given = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", values.get("candidate", ""), flags=re.DOTALL)
                    shown = re.sub(r"'''\s*\*\s*'''|\*", "", given).strip()
                    candidates.append(
                        {
                            "name": clean(shown),
                            "party": link(party) if "party link" in kind else clean(party),
                            "votes": None if unopposed else figure(values.get("votes", "")),
                            "marked": kind.startswith("winning") or shown.startswith("'''"),
                            "incumbent": "*" in given,
                            "unopposed": unopposed,
                        }
                    )
                elif kind.startswith(OUTCOMES):
                    outcomes += 1
                elif kind == "registered electors":
                    electorate = figure(values.get("reg. electors", ""))
            marked = sum(bool(line["marked"]) for line in candidates)
            tacit = all(line["unopposed"] for line in candidates)
            seats = SEATS.get((year, code, division), max(outcomes, marked))
            if tacit:
                seats = len(candidates)
            elif any(line["votes"] is None for line in candidates):
                raise ValueError(f"{title} {division}: a contested candidate has no votes")
            if not seats or seats >= len(candidates) and not tacit:
                raise ValueError(f"{title} {division}: {seats} seats for {len(candidates)} candidates")
            ranked = sorted(candidates, key=lambda line: -int(line["votes"] or 0))
            if not tacit:
                cut = int(ranked[seats - 1]["votes"] or 0)
                if seats < len(ranked) and int(ranked[seats]["votes"] or 0) == cut:
                    raise ValueError(f"{title} {division}: tied at the cut")
                top = {id(line) for line in ranked[:seats]}
                if marked and top != {id(line) for line in candidates if line["marked"]}:
                    if marked == seats:
                        raise ValueError(f"{title} {division}: the marked winners are not the top {seats}")
                    marked = 0
            else:
                top = {id(line) for line in candidates}
            seated[(year, code)] += seats
            divisions.append(
                {
                    "year": year,
                    "code": code,
                    "division": division,
                    "seats": seats,
                    "marked": bool(marked),
                    "electorate": electorate,
                    "revision": revision["revid"],
                }
            )
            for number, line in enumerate(candidates, 1):
                if not line["name"] or not line["party"] and not line["unopposed"]:
                    raise ValueError(f"{title} {division}: candidate {number} has no name or party")
                lines.append(
                    {
                        "year": year,
                        "code": code,
                        "division": division,
                        "number": number,
                        "name": line["name"],
                        "party": line["party"],
                        "votes": line["votes"],
                        "incumbent": line["incumbent"],
                        "elected": id(line) in top,
                    }
                )
        held = seated[(year, code)]
        if code != "E11000006" and held != SIZES[code]:
            raise ValueError(f"{title}: {held} seats against a council of {SIZES[code]}")
    PARSED.mkdir(parents=True, exist_ok=True)
    found_divisions = pl.DataFrame(divisions, infer_schema_length=None)
    found_lines = pl.DataFrame(lines, schema_overrides={"votes": pl.Int64})
    counted = found_lines.filter(pl.col("votes").is_not_null())
    copied = (
        counted.join(counted, on=["code", "name", "votes"])
        .filter(pl.col("year") < pl.col("year_right"))
        .select(pl.concat_list("year", "year_right").alias("year"), "code", "division")
        .explode("year")
        .unique()
    )
    found_divisions.join(copied, on=["year", "code", "division"], how="anti").write_parquet(
        PARSED / "divisions.parquet"
    )
    found_lines.join(copied, on=["year", "code", "division"], how="anti").write_parquet(PARSED / "candidates.parquet")
    copied.sort("code", "division", "year").write_parquet(PARSED / "copied.parquet")


def emit() -> Path:
    divisions = pl.read_parquet(PARSED / "divisions.parquet")
    lines = pl.read_parquet(PARSED / "candidates.parquet")
    copied = pl.read_parquet(PARSED / "copied.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Metropolitan county council elections of 1973, 1977, and 1981 by electoral division, where the "
            "English Wikipedia's election articles tabulate them (Greater Manchester 1973, Merseyside 1973, "
            "1977, and 1981, Tyne and Wear 1973, and part of West Yorkshire 1981), with the six councils as bodies",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        england = cs.polity("GB-ENG")
        nation = cs.area("gb.country", "ENG")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & {*systems.values(), nation}:
            raise ValueError("publish gb.parliament and gb.leh first")
        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/1973_Merseyside_County_Council_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, page()), source)
        if conn.execute("select 1 from scheme where code = 'gb.met_division'").fetchone() is None:
            cs.add(
                "scheme",
                code="gb.met_division",
                polity_id=gb,
                kind="electoral_district",
                rank=3,
                noun="electoral division",
            )

        wikidata = {
            value: id
            for value, id in conn.execute(
                "select e.value, p.id from party p join external_id e on e.entity_id = p.id"
                " where e.scheme = 'wikidata' and e.value = any(%s)"
                " and not exists (select from entity_merge m where m.entity_id = p.id)",
                [list(set(WIKIDATA.values()))],
            ).fetchall()
        }
        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB'"
            " and not exists (select from entity_merge m where m.entity_id = p.id) order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        parties: dict[str, UUID | None] = {}

        def party(label: str) -> UUID | None:
            if label in INDEPENDENT or not label:
                return None
            if label not in parties:
                if label in WIKIDATA:
                    found = wikidata[WIKIDATA[label]]
                else:
                    shown = LABELS.get(label, label)
                    found = None if label in LOCAL else known.get(leh.fold(shown))
                    if found is None:
                        found = cs.key("gb.party", f"{SOURCE}:{slug(shown)}" if label in LOCAL else slug(shown))
                        if found in cs.minted:
                            cs.add("party", id=found, kind="party", polity_id=gb)
                            name(found, shown)
                parties[label] = found
            return parties[label]

        councils: dict[str, tuple[UUID, UUID, UUID, str]] = {}
        for code, county in COUNTIES.items():
            area = cs.area("gb.lad", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.lad")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None, valid_to=None)
            name(area, county)
            polity = cs.key("gb.polity", code)
            path = f"gb/{slug(county)}-county"
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="county", iso_code=None)
                cs.add("polity_parent", polity_id=polity, parent_id=england)
                cs.add("polity_territory", polity_id=polity, area_id=area)
            name(polity, f"{county} County Council")
            cs.add("slug", path=path, entity_id=polity)
            body = cs.key("gb.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
            name(body, f"{county} County Council")
            if code in SIZES:
                cs.add("body_size", body_id=body, seats=SIZES[code], valid_from=DAYS[1973], valid_to=ABOLISHED)
            councils[code] = (area, polity, body, path)

        national: dict[int, UUID] = {}
        for year, day in DAYS.items():
            national[year] = cs.key("gb.election", f"{day}:local:gb")
            if national[year] in cs.minted:
                cs.add(
                    "election",
                    id=national[year],
                    polity_id=gb,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(national[year], f"{year} local elections in England and Wales")
                cs.add("slug", path=f"gb/{day}-local", entity_id=national[year])

        areas: set[UUID] = set()
        for (year, code), title in ARTICLES.items():
            day = DAYS[year]
            area, polity, body, path = councils[code]
            county = COUNTIES[code]
            held = divisions.filter((pl.col("year") == year) & (pl.col("code") == code))
            election = cs.key("gb.election", f"{day}:local:{code}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=national[year],
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{year} {county} County Council election")
            cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
            method = METHOD.format(title=title, revision=held["revision"][0], cited=CITED[code], year=year)
            if held["seats"].sum() < SIZES[code]:
                method += " " + PARTIAL.format(seats=held["seats"].sum(), held=held.height, size=SIZES[code])
            left = copied.filter((pl.col("year") == year) & (pl.col("code") == code))["division"].to_list()
            if left:
                method += COPIED.format(divisions=", ".join(left[:-1]) + ", and " + left[-1])
            cs.add("note", entity_id=election, kind="methodology", body_md=method)
            if (year, code) in NOTES:
                cs.add("note", entity_id=election, kind="caveat", body_md=NOTES[(year, code)])
            for row in held.iter_rows(named=True):
                display = row["division"]
                key = f"{code}:{slug(display)}"
                district = cs.area("gb.met_division", key)
                if district not in areas:
                    areas.add(district)
                    if district in cs.minted:
                        cs.add("area", id=district, scheme_code="gb.met_division")
                        cs.add("area_code", area_id=district, code=key, valid_from=None, valid_to=None)
                        cs.add("area_parent", area_id=district, parent_id=area, valid_from=None, valid_to=None)
                    name(district, display)
                    unit = cs.unit("gb.met_division", key)
                    if unit in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="gb.met_division",
                            code=key,
                            area_relation="equals",
                            area_id=district,
                            kind="standard",
                            valid_from=None,
                        )
                    name(unit, display)
                unit = cs.unit("gb.met_division", key)
                standing = lines.filter(
                    (pl.col("year") == year) & (pl.col("code") == code) & (pl.col("division") == display)
                )
                tacit = standing["votes"].is_null().all()
                seats = row["seats"]
                contest_key = f"{day}:local:{key}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=district,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems["fptp" if seats == 1 else "block"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{display} division, {county} County Council, {year}")
                cs.add("slug", path=f"{path}/{day}-local/{slug(display)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=source,
                    locator={"article": title, "oldid": row["revision"], "box": display},
                )
                if not row["marked"] and not tacit:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="The article does not mark the winner here; the "
                        + (
                            "candidate with the most votes is"
                            if seats == 1
                            else f"{seats} candidates with the most votes are"
                        )
                        + " shown elected.",
                    )
                if row["electorate"]:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=REGISTERED,
                        count_round=0,
                        flags=0,
                        value=row["electorate"],
                    )
                for line in standing.iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    owner = party(line["party"])
                    option = cs.key(f"{SOURCE}.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=line["name"],
                    )
                    person = cs.key(f"{SOURCE}.person", option_key)
                    cs.add("person", id=person)
                    name(person, line["name"])
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=line["name"],
                        member_of_party_id=owner,
                        is_independent=line["party"] in INDEPENDENT,
                        is_incumbent=line["incumbent"] or None,
                    )
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
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="elected",
                            notes=None if row["marked"] or tacit else "Derived from the votes.",
                        )
        return cs.write()
