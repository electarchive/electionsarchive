import io
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.ca.elections import PROVINCES
from ea.store import DATA, connect

SOURCE = "ca.lop"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DATASET = "https://open.canada.ca/data/en/dataset/ea8f2c37-90b6-4fee-857e-984d3060184e"
HFER = "https://open.canada.ca/data/dataset/ea8f2c37-90b6-4fee-857e-984d3060184e/resource/5c8ab22c-4a5e-494b-879a-e0611bc10a95/download/hfer_e.csv"
TITLE = "History of the Federal Electoral Ridings, 1867-2010"
COLUMNS = [
    "Election Date",
    "Election Type",
    "Parliament",
    "Province",
    "Riding",
    "Last Name",
    "First Name",
    "Gender",
    "Party",
    "Votes",
    "Elected",
]
RIDINGS_QUERY = """
SELECT DISTINCT ?item ?en ?fr ?province ?from ?to WHERE {
  { ?item wdt:P31 wd:Q17202187 } UNION { [] p:P39 [ ps:P39 wd:Q15964890 ; pq:P768 ?item ] }
  OPTIONAL { ?item rdfs:label ?en FILTER(lang(?en) = "en") }
  OPTIONAL { ?item rdfs:label ?fr FILTER(lang(?fr) = "fr") }
  OPTIONAL { ?item wdt:P131/rdfs:label ?province FILTER(lang(?province) = "en") }
  OPTIONAL { ?item wdt:P571 ?from }
  OPTIONAL { ?item wdt:P576 ?to }
}
"""
MEMBERS_QUERY = """
SELECT ?mp ?mpLabel ?lop ?district ?start ?end ?birth ?birthPrecision WHERE {
  ?mp p:P39 ?st . ?st ps:P39 wd:Q15964890 ; pq:P768 ?district ; pq:P580 ?start .
  OPTIONAL { ?st pq:P582 ?end }
  OPTIONAL { ?mp wdt:P6616 ?lop }
  OPTIONAL { ?mp p:P569/psv:P569 [ wikibase:timeValue ?birth ; wikibase:timePrecision ?birthPrecision ] }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""
RIDINGS = "https://query.wikidata.org/sparql?" + urlencode({"query": RIDINGS_QUERY, "format": "json"})
MEMBERS = "https://query.wikidata.org/sparql?" + urlencode({"query": MEMBERS_QUERY, "format": "json"})
FIRST_POLL = "2004-06-28"
ORDER_2003 = date(2004, 5, 23)
VACANT = {1: 1}
ORDERS = {
    1: None,
    2: None,
    5: None,
    8: None,
    10: None,
    11: ("Alberta", "Saskatchewan"),
    13: None,
    15: None,
    18: None,
    21: None,
    22: None,
    28: None,
    31: None,
    34: None,
    36: None,
}
PRECISION = {"11": "day", "10": "month", "9": "year"}
CITIES = {
    "Montreal": "Quebec",
    "Toronto": "Ontario",
    "Old Toronto": "Ontario",
    "Ottawa": "Ontario",
    "Pickering": "Ontario",
    "Scarborough": "Ontario",
    "Uxbridge": "Ontario",
    "Calgary": "Alberta",
    "Annapolis County": "Nova Scotia",
}
PINNED_RIDINGS = {
    ("Northwest Territories", "ALBERTA (PROVISIONAL DISTRICT)"): "Q2831695",
    ("Northwest Territories", "SASKATCHEWAN (PROVISIONAL DISTRICT)"): "Q7425172",
    ("Ontario", "OTTAWA (CITY OF)"): "Q3357754",
    ("Quebec", "OTTAWA (COUNTY OF)"): "Q3357755",
    ("Quebec", "SHERBROOKE (TOWN OF)"): "Q3481908",
    ("Saskatchewan", "SASKATOON CITY"): "Q3473806",
    ("Ontario", "STORMONT--DUNDAS--CHARLOTENBURGH"): "Q3499854",
    ("Quebec", "KAMOURASKA--RIVIÈRE-DU-LOUP--TÉMISCOUATA--LES BASQUES"): "Q3192490",
}
REDATED = {("1909-10-26", "Gen", "Alberta", "VICTORIA"): "1908-10-26"}
RELABELED = {
    ("1940-03-26", "PARRY SOUND", "CULBERT"): "New Democracy",
    ("1968-06-25", "VANCOUVER CENTRE", "GOEUJON"): "Reform",
}
SHARED = {
    "Liberal": "LIBERAL",
    "New Democratic Party": "NDP-NEW DEMOCRATIC PARTY",
    "Green Party of Canada": "GREEN PARTY",
    "Bloc Québécois": "BLOC QUÉBÉCOIS",
    "Marxist-Leninist Party": "MARXIST-LENINIST",
    "Communist Party of Canada": "COMMUNIST",
    "Libertarian Party of Canada": "LIBERTARIAN",
    "Christian Heritage Party of Canada": "CHRISTIAN HERITAGE PARTY",
    "Canadian Action Party": "CAP",
    "Marijuana Party": "MARIJUANA PARTY",
}
UNAFFILIATED = ("Unknown", "Independent", "No affiliation to a recognised party")
NUANCES = ("Government", "Opposition", "Opposition-Labour")
COLORS = {
    "Conservative": "#1a4782",
    "Progressive Conservative": "#1a4782",
    "National Government": "#598bca",
    "Liberal-Conservative": "#598bca",
    "Co-operative Commonwealth Federation": "#f37021",
    "Social Credit Party of Canada": "#6b8e23",
    "Ralliement Créditiste": "#9acd32",
    "Progressive": "#c9a227",
    "Reform Party of Canada": "#3cb371",
    "Canadian Alliance": "#5f9ea0",
}
HONORIFICS = re.compile(r"^(?:(?:Rt\.|Right|Hon\.|Sir|Dr\.|Rev\.|Major|Gen\.|Lt\.|Col\.|Capt\.)\s+)+")
SOURCE_NOTE = "Candidates, parties, votes, and winners for every federal general election and by-election from 1867 to 2010, compiled by the Library of Parliament. It has no counts of electors or rejected ballots. From 2004 on, the archive uses Elections Canada's poll-by-poll results instead."
WIKIDATA_NOTE = "Riding names, dates, and Library of Parliament IDs, and members of the House of Commons with their terms. Members are matched to winners by riding, date, and surname."
BLOCK = "Each voter could vote for as many candidates as there were seats. The candidates with the most votes won."
REDATED_NOTE = "The Library of Parliament dates this contest October 26, 1909. The general election was held on October 26, 1908, so the contest is filed under 1908."
RELABELED_NOTE = "The Library of Parliament lists {name} under {printed}, a party founded years later. The archive files the candidacy under {label}, the label used at the time."
MISSING_NOTE = "The Library of Parliament gives no vote count for {names}."


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def loose(text: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9À-Ý]", " ", text.upper().replace("’", "'").replace("'", "")).split())


def fetch() -> None:
    raw.fetch(SOURCE, HFER, suffix=".csv")
    raw.fetch(SOURCE, RIDINGS, suffix=".json")
    raw.fetch(SOURCE, MEMBERS, suffix=".json")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    ridings: dict[str, dict[str, str | None]] = {}
    provinces: dict[str, set[str]] = defaultdict(set)
    for binding in json.loads(raw.cached(SOURCE, RIDINGS).path.read_text(encoding="utf-8"))["results"]["bindings"]:
        values = {field: value["value"].rsplit("/", 1)[-1] for field, value in binding.items()}
        item = values["item"]
        riding = ridings.setdefault(item, {"en": None, "fr": None, "from": "9999", "to": ""})
        riding["en"] = riding["en"] or values.get("en")
        riding["fr"] = riding["fr"] or values.get("fr")
        riding["from"] = min(str(riding["from"]), values.get("from", "")[:10])
        riding["to"] = max(str(riding["to"]), values.get("to", "9999")[:10])
        if "province" in values:
            provinces[item].add(CITIES.get(values["province"], values["province"]))
    named: dict[str, list[str]] = defaultdict(list)
    for item, riding in ridings.items():
        if riding["en"] is not None:
            named[loose(riding["en"])].append(item)

    def locate(province: str, name: str, day: str) -> str:
        if (province, name) in PINNED_RIDINGS:
            return PINNED_RIDINGS[province, name]
        late = (date.fromisoformat(day) + timedelta(days=120)).isoformat()
        for opens in (day, late):
            window = [
                item
                for item in named.get(loose(name), [])
                if str(ridings[item]["from"]) <= opens and day <= str(ridings[item]["to"])
            ]
            if len(window) > 1:
                window = [item for item in window if province in provinces[item]] or window
            if len(window) == 1:
                return window[0]
            if window:
                raise ValueError(f"{name} ({province}) on {day} matches several ridings: {window}")
        raise ValueError(f"{name} ({province}) on {day} matches no riding")

    terms: dict[str, list[tuple[date, date | None, str]]] = defaultdict(list)
    members: dict[str, dict[str, str]] = {}
    for binding in json.loads(raw.cached(SOURCE, MEMBERS).path.read_text(encoding="utf-8"))["results"]["bindings"]:
        values = {field: value["value"].rsplit("/", 1)[-1] for field, value in binding.items()}
        terms[values["district"]].append(
            (
                date.fromisoformat(values["start"][:10]),
                date.fromisoformat(values["end"][:10]) if "end" in values else None,
                values["mp"],
            )
        )
        known = members.get(values["mp"])
        if known is None or int(values.get("birthPrecision", 0)) > int(known.get("birthPrecision", 0)):
            members[values["mp"]] = values

    def similarity(last: str, label: str) -> float:
        surname = re.sub(r"[^a-z]", "", fold(re.split(r",|\s(?:dit|alias)\s", last, flags=re.IGNORECASE)[0]))
        tokens = re.sub(r"[^a-z ]", " ", fold(label)).split()
        joined = [*tokens, *(a + b for a, b in pairwise(tokens))]
        return max((SequenceMatcher(None, surname, token).ratio() for token in joined), default=0)

    text = raw.cached(SOURCE, HFER).path.read_bytes().replace(b"\x81", b"").decode("cp1252")
    records = pl.read_csv(io.BytesIO(text.encode()), columns=COLUMNS, infer_schema=False)
    rows = []
    for record in records.iter_rows(named=True):
        printed = record["Election Date"].replace("/", "-")
        riding = record["Riding"].upper()
        province = record["Province"]
        day = REDATED.get((printed, record["Election Type"], province, riding), printed)
        if day >= FIRST_POLL:
            continue
        last = (record["Last Name"] or "").strip()
        first = " ".join((record["First Name"] or "").split())
        given = HONORIFICS.sub("", first).split(",")[0].strip()
        suffix = re.search(r"\s+(Jr\.|Sr\.|[Jj]un\.)$", given)
        if suffix is not None:
            given = given[: suffix.start()]
        surname = " ".join(
            re.sub(
                r"^Mc(\w)",
                lambda m: f"Mc{m[1].upper()}",
                re.sub(
                    r"\b(\w)'(\w)",
                    lambda m: f"{m[1].upper()}'{m[2].upper()}",
                    re.sub(r"(^|-)(\w)", lambda m: m[1] + m[2].upper(), word.lower()),
                ),
            )
            if word.isupper() and len(word) > 1
            else word
            for word in last.split()
        )
        party = " ".join((record["Party"] or "Unknown").split())
        votes = (record["Votes"] or "").strip()
        district = locate(province, riding, day)
        general = record["Election Type"] == "Gen"
        elected = record["Elected"] == "1"
        held = date.fromisoformat(day)
        mp = None
        if elected:
            scored = {
                member: similarity(last, members[member]["mpLabel"])
                for start, end, member in terms[district]
                if -5 <= (start - held).days <= 120 or (start <= held and (end is None or held <= end))
            }
            best = sorted((score, member) for member, score in scored.items() if score >= 0.8)
            if best and (len(best) == 1 or best[-1][0] > best[-2][0]):
                mp = best[-1][1]
        rows.append(
            {
                "day": held,
                "general": general,
                "parliament": int(record["Parliament"]),
                "order": max(
                    start
                    for start, scope in ORDERS.items()
                    if start <= int(record["Parliament"]) and (scope is None or province in scope)
                ),
                "province": province,
                "district": district,
                "contest": f"{day}:{'general' if general else 'by'}:{district}",
                "group": f"{district}:{fold(last)}|{fold(given)}",
                "name": " ".join(part for part in (given, surname, suffix[1] if suffix else "") if part),
                "gender": {"F": "female", "M": "male"}.get(record["Gender"] or ""),
                "party": RELABELED.get((day, riding, last.upper()), party),
                "printed_party": party,
                "votes": int(votes) if votes.isdigit() else None,
                "acclaimed": votes == "accl.",
                "elected": elected,
                "redated": printed != day,
                "mp": mp,
            }
        )
    candidates = (
        pl.DataFrame(rows, infer_schema_length=None)
        .sort(pl.col("votes").is_null() & ~pl.col("acclaimed"), maintain_order=True)
        .unique(["contest", "group", "party"], keep="first", maintain_order=True)
        .with_columns(
            group=pl.when(pl.len().over("contest", "group") > 1)
            .then(pl.col("group") + "|" + pl.col("party"))
            .otherwise("group"),
            mp=pl.when(pl.len().over("contest", "mp") > 1).then(None).otherwise("mp"),
        )
        .with_columns(
            identity=pl.coalesce(
                "mp",
                pl.when(pl.col("mp").drop_nulls().n_unique().over("group") == 1).then(
                    pl.col("mp").drop_nulls().first().over("group")
                ),
                "group",
            )
        )
    )
    clashes = candidates.filter(pl.len().over("contest", "identity") > 1)
    if clashes.height:
        raise ValueError(
            f"candidates share an identity within a contest: {clashes.select('contest', 'identity', 'name')}"
        )
    candidates.write_parquet(PARSED / "candidates.parquet")
    used = set(candidates["district"])
    pl.DataFrame(
        [(item, riding["en"], riding["fr"]) for item, riding in ridings.items() if item in used],
        schema={"item": pl.String, "en": pl.String, "fr": pl.String},
        orient="row",
    ).write_parquet(PARSED / "ridings.parquet")
    matched = set(candidates["identity"])
    pl.DataFrame(
        [
            (
                mp,
                values["mpLabel"],
                values.get("lop"),
                None if values.get("birth", "-").startswith("-") else values["birth"][:10],
                PRECISION.get(values.get("birthPrecision", "")),
            )
            for mp, values in members.items()
            if mp in matched
        ],
        schema={"mp": pl.String, "label": pl.String, "lop": pl.String, "birth": pl.String, "precision": pl.String},
        orient="row",
    ).write_parquet(PARSED / "members.parquet")
    unmatched = candidates.filter(pl.col("elected") & pl.col("mp").is_null())
    print(
        f"{candidates.height} candidacies, {candidates['contest'].n_unique()} contests, "
        f"{unmatched.height} winners without a Wikidata member"
    )


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    ridings = {item: (en, fr) for item, en, fr in pl.read_parquet(PARSED / "ridings.parquet").iter_rows()}
    members = {row["mp"]: row for row in pl.read_parquet(PARSED / "members.parquet").iter_rows(named=True)}
    province_codes = {label: code for code, (_, label) in PROVINCES.items()}

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Canada's federal general elections and by-elections from 1867 to 2004 by riding",
            polity="CA",
        )

        def name(entity: UUID, text: str, kind: str = "official", lang: str = "en") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def note(entity: UUID, body: str, kind: str = "caveat") -> None:
            cs.add("note", entity_id=entity, kind=kind, body_md=body)

        library = cs.source("Library of Parliament", TITLE)
        cs.add(
            "source",
            id=library,
            kind="official_body",
            publisher="Library of Parliament",
            title=TITLE,
            url=DATASET,
            license="Open Government Licence – Canada",
            license_url="https://open.canada.ca/en/open-government-licence-canada",
            notes=SOURCE_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, HFER), library)
        wikidata = cs.source("Wikidata", "Federal electoral districts and members of the House of Commons of Canada")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Federal electoral districts and members of the House of Commons of Canada",
            url="https://www.wikidata.org/wiki/Q15964890",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            notes=WIKIDATA_NOTE,
        )
        for url in (RIDINGS, MEMBERS):
            cs.artifact(raw.cached(SOURCE, url), wikidata)

        ca = cs.polity("CA")
        commons = cs.key("ca.elections.body", "house_of_commons")
        fptp = cs.key("ca.elections.electoral_system", "fptp")
        block = cs.key(f"{SOURCE}.electoral_system", "block")
        if block in cs.minted:
            cs.add("electoral_system", id=block, polity_id=ca, rules={"formula": "block"}, rules_version=1)
            name(block, "Block vote")
            note(block, BLOCK, "methodology")

        firsts = dict(candidates.filter("general").group_by("parliament").agg(pl.col("day").min()).iter_rows())
        areas: dict[tuple[str, int], UUID] = {}
        units: dict[tuple[str, int], UUID] = {}
        spans: dict[str, set[int]] = defaultdict(set)
        for item, order in candidates.select("district", "order").unique().iter_rows():
            spans[item].add(order)
        for item, order, province in (
            candidates.select("district", "order", "province")
            .unique(["district", "order"], maintain_order=True)
            .iter_rows()
        ):
            en, fr = ridings[item]
            start = firsts[order]
            following = [
                *(later for later, scope in ORDERS.items() if later > order and (scope is None or province in scope)),
                *(later for later in spans[item] if later > order),
            ]
            end = firsts[min(following)] if following else ORDER_2003
            area = areas[item, order] = cs.area("ca.fed", item, start)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="ca.fed")
                cs.add("area_code", area_id=area, code=item, valid_from=start, valid_to=end)
                cs.add("area_parent", area_id=area, parent_id=cs.area("ca.province", province_codes[province]))
                name(area, en)
                if fr is not None and fr != en:
                    name(area, fr, "native", "fr")
            units[item, order] = cs.unit("ca.fed", item, start)
            cs.add(
                "reporting_unit",
                id=units[item, order],
                polity_id=ca,
                scheme_code="ca.fed",
                code=item,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=start,
                valid_to=end,
            )

        parties: dict[str, UUID | None] = {}
        for label in candidates["party"].unique(maintain_order=True):
            if label in UNAFFILIATED:
                parties[label] = None
            elif label in SHARED:
                parties[label] = cs.key("ca.elections.party", SHARED[label])
            else:
                party = parties[label] = cs.key(f"{SOURCE}.party", label)
                if party in cs.minted:
                    kind = "nuance" if label.startswith("Independent ") or label in NUANCES else "party"
                    cs.add("party", id=party, kind=kind, polity_id=ca)
                    name(party, label)
                if label in COLORS:
                    cs.add("party_color", party_id=party, color=COLORS[label])

        persons: dict[str, UUID] = {}
        for identity, label, gender in (
            candidates.group_by("identity", maintain_order=True)
            .agg(pl.col("name").first(), pl.col("gender").drop_nulls().first())
            .iter_rows()
        ):
            if re.fullmatch(r"Q\d+", identity):
                persons[identity] = cs.key("wikidata", identity)
                if persons[identity] in cs.minted:
                    details = members[identity]
                    cs.add(
                        "person",
                        id=persons[identity],
                        gender=gender,
                        birth_date=None if details["birth"] is None else date.fromisoformat(details["birth"]),
                        birth_precision=details["precision"],
                    )
                    name(persons[identity], label if details["label"] == identity else details["label"])
                    if details["lop"] is not None:
                        cs.add("external_id", entity_id=persons[identity], scheme="lop.person", value=details["lop"])
            else:
                persons[identity] = cs.key(f"{SOURCE}.person", identity)
                if persons[identity] in cs.minted:
                    cs.add("person", id=persons[identity], gender=gender)
                    name(persons[identity], label)

        generals = candidates.filter("general")
        lasts = dict(generals.group_by("parliament").agg(pl.col("day").max()).iter_rows())
        mains = {
            parliament: Counter(days).most_common(1)[0][0]
            for parliament, days in generals.unique("contest").group_by("parliament").agg("day").iter_rows()
        }
        elections: dict[tuple[date, bool], UUID] = {}
        for parliament, main in sorted(mains.items()):
            elections[main, True] = cs.key(f"{SOURCE}.election", f"{main}:general")
            cs.add(
                "election",
                id=elections[main, True],
                polity_id=ca,
                kind="general",
                date_from=firsts[parliament],
                date_to=lasts[parliament],
                status="held",
            )
            name(elections[main, True], f"{main.year} Canadian federal election")
            cs.add("slug", path=f"ca/{main}-general", entity_id=elections[main, True])
        for day, general, parliament, count in (
            candidates.group_by("day", "general", "parliament")
            .agg(pl.col("district").n_unique())
            .sort("day", "general")
            .iter_rows()
        ):
            if (day, general) in elections:
                continue
            election = elections[day, general] = cs.key(f"{SOURCE}.election", f"{day}:{'general' if general else 'by'}")
            spoken = f"{day:%B} {day.day}, {day.year}"
            if general:
                cs.add(
                    "election",
                    id=election,
                    polity_id=ca,
                    parent_id=elections[mains[parliament], True],
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{mains[parliament].year} Canadian federal election, polling of {spoken}")
                cs.add("slug", path=f"ca/{day}-general", entity_id=election)
            else:
                cs.add(
                    "election", id=election, polity_id=ca, kind="by_election", date_from=day, date_to=day, status="held"
                )
                only = candidates.filter((pl.col("day") == day) & ~pl.col("general"))["district"][0]
                name(
                    election,
                    f"Canadian federal by-elections, {spoken}"
                    if count > 1
                    else f"{ridings[only][0]} federal by-election, {spoken}",
                )
                cs.add("slug", path=f"ca/{day}-by-election", entity_id=election)

        order = sorted(firsts)
        starts = [firsts[parliament] for parliament in order]
        for parliament, start, end in zip(order, starts, [*starts[1:], ORDER_2003], strict=True):
            seats = int(generals.filter(pl.col("parliament") == parliament)["elected"].sum()) + VACANT.get(
                parliament, 0
            )
            cs.add("body_size", body_id=commons, seats=seats, valid_from=start, valid_to=end)

        scoped = candidates.with_columns(
            scope=pl.when("general").then(pl.col("parliament").cast(pl.String)).otherwise(pl.col("day").cast(pl.String))
        )
        repeated = {
            pair
            for pair, count in Counter(
                (scope, ridings[district][0])
                for scope, district in scoped.select("scope", "district").unique().iter_rows()
            ).items()
            if count > 1
        }
        for (key,), frame in scoped.group_by("contest", maintain_order=True):
            first = frame.row(0, named=True)
            day, general, item = first["day"], first["general"], first["district"]
            en = ridings[item][0]
            label = f"{en} ({first['province']})" if (first["scope"], en) in repeated else en
            year = mains[first["parliament"]].year if general else day.year
            magnitude = int(frame["elected"].sum())
            tacit = bool(frame["acclaimed"].all()) or bool(frame["votes"].is_null().all() and frame["elected"].all())
            contest = cs.key(f"{SOURCE}.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[day, general],
                kind="body_seats",
                body_id=commons,
                district_area_id=areas[item, first["order"]],
                magnitude=magnitude,
                electoral_system_id=fptp if magnitude == 1 else block,
                default_result_kind="tacit" if tacit else "counted",
            )
            name(contest, f"{year} {'election' if general else 'by-election'} in {label}")
            cs.add("slug", path=f"ca/{day}-{'general' if general else 'by-election'}/{slug(label)}", entity_id=contest)
            if frame["redated"].any() and contest in cs.minted:
                note(contest, REDATED_NOTE)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            missing = [] if tacit else frame.filter(pl.col("votes").is_null() & ~pl.col("acclaimed"))["name"].to_list()
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[day, general],
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=library,
                is_default=True,
                completeness=None if missing else 1,
            )
            if missing and result_set in cs.minted:
                listed = (
                    missing[0]
                    if len(missing) == 1
                    else f"{missing[0]} and {missing[1]}"
                    if len(missing) == 2
                    else f"{', '.join(missing[:-1])}, and {missing[-1]}"
                )
                note(result_set, MISSING_NOTE.format(names=listed))
            for row in frame.iter_rows(named=True):
                option = cs.key(f"{SOURCE}.option", f"{key}:{row['identity']}")
                party = parties[row["party"]]
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=row["name"],
                )
                candidacy = cs.key(f"{SOURCE}.candidacy", f"{key}:{row['identity']}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=persons[row["identity"]],
                    option_id=option,
                    role="head",
                    ballot_name=row["name"],
                    member_of_party_id=None
                    if party is None or row["party"].startswith("Independent ") or row["party"] in NUANCES
                    else party,
                    is_independent=row["party"].startswith("Independent"),
                )
                if row["party"] != row["printed_party"] and candidacy in cs.minted:
                    note(
                        candidacy,
                        RELABELED_NOTE.format(name=row["name"], printed=row["printed_party"], label=row["party"]),
                    )
                if row["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                if result_set in cs.minted and row["votes"] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[item, first["order"]],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=row["votes"],
                    )
        return cs.write()
