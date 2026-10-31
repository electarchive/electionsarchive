import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.wikipedia_primaries import API, STATES, clean
from ea.store import DATA, connect

SOURCE = "us.wikipedia_caucuses"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = (2004, 2008, 2012, 2016, 2020)
PLACES = STATES | {
    "DC": "District of Columbia",
    "LA": "Louisiana",
    "PR": "Puerto Rico",
    "GU": "Guam",
    "VI": "United States Virgin Islands",
    "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}
EVENTS = ("caucuses", "caucus", "primary", "primaries", "convention")
TEMPLATE = r"\{\{\s*(\d{4} ?[A-Z]{2} ?[A-Za-z]+)\s*\}\}"
NOTE = (
    "Statewide results of presidential caucuses and party-run primaries as transcribed in the English Wikipedia's "
    "article on each event, or in its state section of the party's results article, which cite the state parties "
    "and news tallies."
)
METHOD = (
    "Statewide totals from the results table in the English Wikipedia's {article}, as fetched in October 2026. "
    "National delegates appear as seats won where the table gives the actual count, not an estimate."
)
VOTES = {(2004, "Democratic", "NM"), (2016, "Democratic", "KS"), (2008, "Democratic", "ND"), (2008, "Republican", "ME")}
ALIASES = {
    "Joe Lieberman": "Joseph Lieberman",
    "Richard Gephardt": "Richard A. Gephardt",
    "Rocky De La Fuente": 'Roque "Rocky" De La Fuente',
    "Buddy Roemer": "Charles Elson Roemer",
    "Pat Buchanan": "Patrick Buchanan",
}
MONTHS = r"(?:January|February|March|April|May|June|July|August)"
OFFICIAL = r"^https?://(?:[\w-]+\.)*(?:[\w-]+\.(?:gov|us)|[\w-]*(?:democrat|republican|gop)[\w-]*\.\w+|demnpl\.com|ksdp\.org|ceepur\.org)(?:[:/]|$)"


def targets() -> list[tuple[int, str, str, str]]:
    with connect() as conn:
        held = set(
            conn.execute(
                """select substr(x.value, 1, 4)::int, initcap(split_part(x.value, ':', 3)), substr(p.iso_code, 4)
                   from external_id x join contest k on k.id = x.entity_id
                   join contest c on c.allocation_parent_id = k.id
                   join election e on e.id = c.election_id join polity p on p.id = e.polity_id
                   where x.scheme = 'us.contest'
                     and x.value ~ '^20(04|08|12|16|20)-[0-9-]+:president:(democratic|republican)$'
                     and c.changeset_id not in (select id from changeset where pipeline = %s)""",
                [SOURCE],
            ).fetchall()
        )
    return [
        (year, party, state, f"{year} {name} {party} presidential {event}")
        for year in YEARS
        for party in ("Democratic", "Republican")
        for state, name in PLACES.items()
        if (year, party, state) not in held
        for event in EVENTS
    ]


def fetch() -> None:
    wanted = targets()
    resolved: dict[str, str] = {}
    titles = sorted({title for *_, title in wanted})
    for index in range(0, len(titles), 50):
        batch = titles[index : index + 50]
        url = API + urlencode(
            {"action": "query", "titles": "|".join(batch), "redirects": 1, "format": "json", "formatversion": 2}
        )
        answer = json.loads(raw.fetch(SOURCE, url, suffix=".json").path.read_text())["query"]
        hops = {hop["from"]: hop["to"] for key in ("normalized", "redirects") for hop in answer.get(key, [])}
        present = {found["title"] for found in answer["pages"] if not found.get("missing")}
        for title in batch:
            target = hops.get(hops.get(title, title), hops.get(title, title))
            if target in present:
                resolved[title] = target
    texts: dict[str, str] = {}
    batches: list[str] = []
    pages = sorted(
        set(resolved.values())
        | {
            f"{prefix}{year} {party} Party presidential primaries"
            for prefix in ("", "Results of the ")
            for year in YEARS
            for party in ("Democratic", "Republican")
        }
    )
    for _ in range(2):
        for index in range(0, len(pages), 50):
            url = API + urlencode(
                {
                    "action": "query",
                    "prop": "revisions",
                    "rvprop": "content",
                    "rvslots": "main",
                    "titles": "|".join(pages[index : index + 50]),
                    "redirects": 1,
                    "format": "json",
                    "formatversion": 2,
                }
            )
            batches.append(url)
            for found in json.loads(raw.fetch(SOURCE, url, suffix=".json").path.read_text())["query"]["pages"]:
                if not found.get("missing"):
                    texts[found["title"]] = found["revisions"][0]["slots"]["main"]["content"]
        pages = sorted(
            {f"Template:{name}" for text in texts.values() for name in re.findall(TEMPLATE, text)} - set(texts)
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "targets.json").write_text(
        json.dumps(
            {
                "wanted": sorted({(year, party, state) for year, party, state, _ in wanted}),
                "articles": [
                    (year, party, state, resolved[title]) for year, party, state, title in wanted if title in resolved
                ],
                "batches": batches,
            }
        )
    )


def parse() -> None:
    manifest = json.loads((PARSED / "targets.json").read_text())
    texts: dict[str, str] = {}
    hops: dict[str, str] = {}
    for url in manifest["batches"]:
        answer = json.loads(raw.cached(SOURCE, url).path.read_text())["query"]
        hops |= {hop["from"]: hop["to"] for key in ("normalized", "redirects") for hop in answer.get(key, [])}
        for found in answer["pages"]:
            if not found.get("missing"):
                texts[found["title"]] = found["revisions"][0]["slots"]["main"]["content"]
    for alias, target in hops.items():
        target = hops.get(target, target)
        if target in texts:
            texts.setdefault(alias, texts[target])
    articles = {(year, party, state): title for year, party, state, title in manifest["articles"]}
    rows: list[dict[str, object]] = []
    for year, party, state in manifest["wanted"]:
        place = PLACES[state].replace("United States ", "")
        article = articles.get((year, party, state))
        if article is not None and not (
            str(year) in article
            and party[:3] in article
            and re.search(re.escape(place) + "|U.S. Virgin|Washington, D.C.", article)
        ):
            article = None
        documents = [(article, texts[article])] if article in texts else []
        for overview in (
            f"Results of the {year} {party} Party presidential primaries",
            f"{year} {party} Party presidential primaries",
        ):
            whole = texts.get(overview, "")
            for heading in re.finditer(r"^(=+)\s*([^=\n]+?)\s*=+\s*$", whole, re.MULTILINE):
                if not re.fullmatch(
                    {
                        "VI": r"(?:U\.?S\.? |United States )?Virgin Islands",
                        "DC": r"District of Columbia|Washington,? D\.?C\.?",
                        "MP": r"Northern Mariana(?:s| Islands)",
                    }.get(state, re.escape(place)),
                    heading[2].replace("[", "").replace("]", ""),
                ):
                    continue
                closing = re.search(rf"^={{2,{len(heading[1])}}}[^=]", whole[heading.end() :], re.MULTILINE)
                documents.append(
                    (overview, whole[heading.end() : heading.end() + (closing.start() if closing else len(whole))])
                )
                break
        chosen = None
        for source, text in documents:
            for name in dict.fromkeys(re.findall(TEMPLATE, text)):
                body = re.sub(r"<noinclude>.*?</noinclude>", "", texts.get(f"Template:{name}", ""), flags=re.DOTALL)
                text = re.sub(r"\{\{\s*" + re.escape(name) + r"\s*\}\}", lambda _, body=body: body, text)
            text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
            text = re.sub(
                r"\{\{\s*election ?table\s*\|[^|{}]*\|((?:\[\[[^\]]*\]\]|[^{}])*)\}\}",
                r"{|\n|+\1",
                text,
                flags=re.IGNORECASE,
            )
            text = re.sub(
                r"<ref[^>]*/\s*>|<ref(?:\s[^>]*[^/])?>(.*?)</ref>",
                lambda ref: f"⟦{(re.search(r'https?://[^\s|}\]]+', ref[1] or '') or [''])[0]}⟧",
                text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            text = re.sub(r"\[\[(?:File|Image):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
            stated = re.search(rf"({MONTHS} \d{{1,2}}),? ({year})", text)
            started = re.search(rf"\{{\{{\s*start date\s*\|\s*({year})\s*\|\s*(\d+)\s*\|\s*(\d+)", text, re.IGNORECASE)
            reversed_ = re.search(rf"(\d{{1,2}}) ({MONTHS}),? ({year})", text)
            day = (
                date(year, int(started[2]), int(started[3]))
                if started
                else date.strptime(f"{stated[1]}, {stated[2]}", "%B %d, %Y")
                if stated
                else date.strptime(f"{reversed_[2]} {reversed_[1]}, {reversed_[3]}", "%B %d, %Y")
                if reversed_
                else None
            )
            for table in re.finditer(r"^\{\|.*?^\|\}", text, re.DOTALL | re.MULTILINE):
                body = re.sub(r"\{\{\s*party (?:color|shading)[^{}]*\}\}", "", table[0], flags=re.IGNORECASE)
                body = re.sub(
                    r"\{\{\s*sortname\s*\|([^|{}]*)\|([^|{}]*)[^{}]*\}\}", r"\1 \2", body, flags=re.IGNORECASE
                )
                body = re.sub(r"\{\{\s*(?:nts|ntsh|formatnum)\s*[|:]\s*([^{}|]*)\}\}", r"\1", body, flags=re.IGNORECASE)
                cited = [
                    *re.findall(r"⟦(https?://[^⟧]+)⟧", body),
                    *re.findall(r"\[(https?://[^\s\]]+)", body),
                    *reversed(re.findall(r"⟦(https?://[^⟧]+)⟧", text[: table.start()])),
                ]
                body = re.sub(r"⟦[^⟧]*⟧", "", body)
                body = re.sub(r"<br\s*/?>", " ", body, flags=re.IGNORECASE)
                caption = re.search(r"^\|\+(.*)$", body, re.MULTILINE)
                label = clean(re.sub(r"^[^|]*=[^|]*\|(?!\|)", "", caption[1])) if caption else ""
                if re.search(
                    r"\bby (count(y|ies)|(congressional )?district|town|precinct)|^(count(y|ies)|district(?! of columbia))\b|poll|endorse|delegate selection",
                    label,
                    re.IGNORECASE,
                ):
                    continue
                heads: list[list[tuple[str, int, int]]] = []
                lines: list[list[str]] = []
                for chunk in re.split(r"^\|-.*$", body, flags=re.MULTILINE):
                    cells: list[tuple[str, int, int, bool]] = []
                    for line in chunk.split("\n"):
                        if not line.startswith(("|", "!")) or line.startswith(("|+", "|}", "{|")):
                            continue
                        for cell in re.split(r"\|\||!!", line[1:]):
                            width = re.search(r'colspan\s*=\s*"?(\d+)', cell)
                            height = re.search(r'rowspan\s*=\s*"?(\d+)', cell)
                            cell = re.sub(
                                r"^(?:\s*(?:scope|style|colspan|rowspan|class|align|width|bgcolor|data-sort-value|data-sort-type)\s*=\s*[^|]*)+\|",
                                "",
                                cell,
                            )
                            cell = re.sub(r"^[^|\[{]*\|(?!\|)", "", cell)
                            cells.append(
                                (
                                    clean(cell),
                                    int(width[1]) if width else 1,
                                    int(height[1]) if height else 1,
                                    line[0] == "!",
                                )
                            )
                    if not cells:
                        continue
                    if all(head for *_, head in cells) and not lines and len(cells) == 1 and cells[0][1] > 1:
                        label = label or cells[0][0]
                        continue
                    if all(head for *_, head in cells) and not lines and len(heads) < 2:
                        heads.append([(text_, width, height) for text_, width, height, _ in cells])
                        continue
                    lines.append([text_ for text_, *_ in cells])
                header: list[str] = []
                subs = [text_ for text_, _, _ in heads[1]] if len(heads) > 1 else []
                for text_, width, height in heads[0] if heads else []:
                    if width > 1 and height == 1 and subs:
                        header += [f"{text_} {subs.pop(0) if subs else ''}".strip() for _ in range(width)]
                    else:
                        header += [text_] * width
                if not header:
                    continue
                lowered = [cell.lower() for cell in header]
                who = next(
                    (i for i, cell in enumerate(lowered) if re.search(r"candidate|^name$|choice|preference", cell)),
                    None,
                )
                while who is not None and who + 1 < len(lowered) and lowered[who + 1] == lowered[who]:
                    who += 1
                measure, value = None, None
                for pattern, code in (
                    (r"state delegate equivalents|^sdes?\b", 12),
                    (r"^first alignment", 17),
                    (r"^(popular )?votes?\b|vote count|^popular vote|straw poll|^first round|ballots", 1),
                    (
                        (
                            r"(county|district|state|convention|precinct|legislative district|regional) ?(caucus ?|convention ?)?delegates"
                            r"|delegates to the (state|county|district)"
                        ),
                        0,
                    ),
                ):
                    value = next(
                        (i for i, cell in enumerate(lowered) if re.search(pattern, cell) and "%" not in cell), None
                    )
                    if value is not None:
                        measure = code
                        break
                seats = [
                    i
                    for i, cell in enumerate(lowered)
                    if re.search(r"delegate", cell)
                    and not re.search(r"state delegate|equivalent|county|precinct|district|convention|%", cell)
                ]
                seat = next((i for i in seats if "total" in lowered[i]), seats[-1] if seats else None)
                if seat is not None and re.search(r"estimat|project|potential|expect", lowered[seat]):
                    seat = None
                if who is None or value is None and seat is None:
                    continue
                if (year, party, state) in VOTES:
                    measure = 1
                if chosen is None or chosen[5] == 0 and measure != 0:
                    chosen = (label, header, lines, who, value, measure, seat, cited)
                if measure != 0:
                    break
            if chosen is not None and chosen[5] != 0:
                break
        if chosen is None:
            print(f"{year} {party} {state}: {[source for source, _ in documents]}: no results table")
            continue
        label, header, lines, who, value, measure, seat, cited = chosen
        final = next((i for i, cell in enumerate(header) if re.match(r"final alignment", cell, re.IGNORECASE)), None)
        convention = (
            next(
                (i for i, cell in enumerate(header) if re.match(r"county ?convention ?delegates", cell, re.IGNORECASE)),
                None,
            )
            if measure == 17
            else value
            if measure == 0
            else None
        )
        level = (
            re.search(r"precinct|county|district|state|regional", header[convention].lower()) if convention else None
        )
        tier = (
            None
            if level is None
            else "precinct delegates"
            if level[0] == "precinct"
            else "regional caucus delegates"
            if level[0] == "regional"
            else f"{level[0]} convention delegates"
        )
        if state == "IA" and party == "Democratic" and measure == 0:
            measure = 12
        dated = re.search(rf"({MONTHS} \d{{1,2}})(?:[–-]\d{{1,2}})?,? ({year})", label)
        if dated:
            day = date.strptime(f"{dated[1]}, {dated[2]}", "%B %d, %Y")
        if day is None:
            print(f"{year} {party} {state}: {source}: no date")
            continue
        total = None
        for cells in lines:
            if len(cells) <= who:
                continue
            candidate = re.sub(
                r"\s*\((?:withdr[ae]wn?|incumbent|write-?in)[^)]*\)|\s*\[[a-z0-9 ]+\]$", "", cells[who]
            ).strip(" *'")
            candidate = re.sub(r"\s*\([^)]*\)$", "", candidate)
            number = cells[value] if value is not None and value < len(cells) else ""
            delegates = cells[seat] if seat is not None and seat < len(cells) else ""
            amount = re.sub(r"[^\d.]", "", number) if not re.search(r"%", number) else ""
            if re.match(
                r"\(?available|ex officio|unpledged|superdelegate|inactive|exhausted|pending|not yet|vacant|blank|void"
                r"|null|spoiled|over ?votes|under ?votes|invalid|provisional|unsigned",
                candidate,
                re.IGNORECASE,
            ):
                continue
            if any(
                re.match(r"(grand )?totals?\b|^total:|unprojected|turnout|registered", cell, re.IGNORECASE)
                for cell in cells[: who + 1]
            ) or re.fullmatch(r"[\d.,%\s]+", candidate):
                total = float(amount) if amount else total
                continue
            if not candidate or not (amount or re.sub(r"\D", "", delegates)):
                continue
            kind = (
                "uncommitted"
                if re.match(
                    r"uncommitted|noncommitted|no preference|uninstructed|undecided|undeclared",
                    candidate,
                    re.IGNORECASE,
                )
                else "write_in"
                if re.search(r"write.?in", candidate, re.IGNORECASE)
                else "none_of_the_above"
                if re.match(r"none of", candidate, re.IGNORECASE)
                else "others"
                if re.fullmatch(r"others?|other candidates|scattered|scattering", candidate, re.IGNORECASE)
                else "candidate"
            )
            rows.append(
                {
                    "year": year,
                    "party": party,
                    "state": state,
                    "article": source,
                    "label": label,
                    "day": day,
                    "caucus": bool(re.search(r"caucus|convention", f"{article or ''} {label}", re.IGNORECASE)),
                    "candidate": candidate,
                    "value": float(amount) if amount else None,
                    "measure": measure if amount else None,
                    "delegates": int(re.sub(r"\D", "", delegates)) if re.sub(r"\D", "", delegates) else None,
                    "kind": kind,
                    "cited": cited[0] if cited else None,
                    "columns": " | ".join(header),
                    "tier": tier,
                    "final": float(re.sub(r"[^\d.]", "", cells[final]) or 0)
                    if final is not None and final < len(cells)
                    else None,
                    "convention": float(re.sub(r"[^\d.]", "", cells[convention]) or 0)
                    if measure == 17 and convention is not None and convention < len(cells)
                    else None,
                }
            )
        counted = sum(
            float(str(row["value"]))
            for row in rows
            if (row["year"], row["party"], row["state"]) == (year, party, state) and row["value"] is not None
        )
        for row in rows:
            if (row["year"], row["party"], row["state"]) == (year, party, state):
                row["total"] = total if total is not None and total >= counted else None
                if counted == 0:
                    row["value"] = row["measure"] = None
    frame = pl.DataFrame(rows, infer_schema_length=None)
    frame.write_parquet(PARSED / "presidential.parquet")
    for (year, party), group in frame.group_by("year", "party", maintain_order=True):
        print(year, party, group["state"].n_unique(), sorted(group["state"].unique()))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "presidential.parquet")
    manifest = json.loads((PARSED / "targets.json").read_text())
    with connect() as conn:
        nations = {
            (int(key[:4]), key.rsplit(":", 1)[1]): (contest, election)
            for key, contest, election in conn.execute(
                """select x.value, k.id, k.election_id from external_id x join contest k on k.id = x.entity_id
                   where x.scheme = 'us.contest'
                     and x.value ~ '^20(04|08|12|16|20)-[0-9-]+:president:(democratic|republican)$'"""
            ).fetchall()
        }
        measures = dict(conn.execute("select code, id from measure").fetchall())
        slugs = dict(conn.execute("select path, entity_id from slug").fetchall())
        areas: dict[str, UUID] = {}
        units = {
            state: unit
            for state, unit, areas[state] in conn.execute(
                """select substr(p.iso_code, 4), u.id, u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        weights: dict[UUID, int] = {}
        pool = [
            (year, party, text, person)
            for year, party, text, person, weights[person] in conn.execute(
                """select q.*, (select count(*) from candidacy y where y.person_id = q.person)
                   from (select coalesce(extract(year from e.date_from)::int, 0), split_part(x.value, ':', 3), n.text,
                          coalesce(m.into_id, cy.person_id) as person
                   from external_id x join contest k on k.id = x.entity_id
                   join contest c on c.allocation_parent_id = k.id or c.id = k.id
                   join election e on e.id = c.election_id
                   join contest_option o on o.contest_id = c.id
                   join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   left join entity_merge m on m.entity_id = cy.person_id
                   join name n on n.entity_id = coalesce(m.into_id, cy.person_id) and n.is_preferred
                   where x.scheme = 'us.contest' and x.value ~ ':president:(democratic|republican)$'
                   group by 1, 2, 3, 4) q"""
            ).fetchall()
        ]
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Presidential caucuses and party-run primaries 2004–2020 that the FEC's workbooks leave out, "
                "from the English Wikipedia, as state delegate allocations under each party's nomination"
            ),
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source("Wikipedia", "Presidential caucuses and party-run primaries, 2004–2020")
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title="Presidential caucuses and party-run primaries, 2004–2020",
            url="https://en.wikipedia.org/wiki/United_States_presidential_primary",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        for url in manifest["batches"]:
            cs.artifact(raw.cached(SOURCE, url), source)

        def split(text: str) -> tuple[str, str]:
            parts = [
                word
                for word in slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", text)).split("-")
                if word not in {"jr", "sr", "ii", "iii", "iv", ""}
            ] or ["", ""]
            return parts[0], parts[-1]

        people: dict[str, UUID] = {}
        values: list[tuple[str, str, str, float, int]] = []
        held = 0
        for (year, party, state), lines in frame.sort("year", "party", "state").group_by(
            "year", "party", "state", maintain_order=True
        ):
            lower = party.lower()
            code = lines["measure"].drop_nulls().first()
            measure = (
                {1: 1, 12: 12, 17: 17, 0: measures.get("caucus_delegates")}.get(code) if code is not None else None
            )
            if code == 0 and measure is None:
                held += 1
                continue
            day = lines["day"][0]
            caucus = bool(lines["caucus"][0])
            nation, umbrella = nations[(year, lower)]
            place = PLACES[state]
            event = "caucuses" if caucus else "presidential-primary"
            election_key = f"{day}:{state}:{lower}-{event}"
            election = cs.key("us.election", election_key)
            cs.add(
                "election",
                id=election,
                polity_id=cs.polity(f"US-{state}"),
                parent_id=umbrella,
                kind="caucus" if caucus else "primary",
                date_from=day,
                date_to=day,
                status="held",
            )
            title = f"{year} {place} {party} " + ("caucuses" if caucus else "presidential primary")
            name(election, title)
            cs.add("slug", path=f"us/{state.lower()}/{day}-{lower}-{event}", entity_id=election)
            contest_key = f"{day}:{state}:president:{lower}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="delegate_allocation",
                office_id=cs.key("us.office", "president"),
                district_area_id=areas[state],
                party_scope_id=cs.key("us.party", f"{lower}-party"),
                allocation_parent_id=nation,
                round_number=1,
                default_result_kind="counted",
            )
            name(contest, title)
            path = f"us/{state.lower()}/{day}-{lower}-{event}/president"
            if slugs.setdefault(path, contest) != contest:
                raise ValueError(f"slug {path} is taken")
            cs.add("slug", path=path, entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            counted = float(lines["value"].fill_null(0).sum())
            total = lines["total"][0]
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=0 if measure is None else 1 if total is None else min(1, round(counted / total, 4)),
            )
            article = lines["article"][0]
            cited = lines["cited"][0] if lines["cited"][0] and re.match(OFFICIAL, lines["cited"][0]) else None
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=(
                    METHOD.format(article=article)
                    + {
                        None: " The caucus reported national delegates only, so no votes are recorded.",
                        1: "",
                        12: " Iowa Democrats report state delegate equivalents, not votes.",
                        17: (
                            f" The party reported each candidate's first and final alignments and the "
                            f"{lines['tier'][0]} each won."
                        ),
                    }.get(measure, f" The party reported the {lines['tier'][0]} each candidate won, not votes.")
                    + (f" The article cites {cited}." if cited else "")
                ),
            )
            if state in {"AK", "KS", "WY"} and year == 2020 and lower == "democratic":
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        "The party counted ranked ballots; these are the final-round totals after the transfers, "
                        "and ballots that ran out of choices are not counted."
                    ),
                )
            if total is not None and total > counted:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"The article's total line counts {total - counted:,.0f} more than its candidate rows.",
                )
            for candidate, kind_of, number, delegates, final, convention in lines.select(
                "candidate", "kind", "value", "delegates", "final", "convention"
            ).iter_rows():
                option_key = f"{contest_key}:{slug(candidate)}"
                option = cs.key("us.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind=kind_of,
                    party_id=cs.key("us.party", f"{lower}-party") if kind_of == "candidate" else None,
                    ballot_label=candidate,
                )
                if number is not None and measure is not None:
                    values.append((str(result_set), str(units[state]), str(option), number, measure))
                for extra, code in ((final, 18), (convention, measures.get("caucus_delegates"))):
                    if extra is not None and code is not None:
                        values.append((str(result_set), str(units[state]), str(option), extra, code))
                if delegates:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=delegates)
                if kind_of != "candidate":
                    continue
                if candidate not in people:
                    first, surname = split(candidate)
                    matches = [
                        (when, side, person)
                        for when, side, text, person in pool
                        if split(text)[1] == surname
                        and (split(text)[0].startswith(first) or first.startswith(split(text)[0]))
                    ]
                    found = {person for when, side, person in matches if (when, side) == (year, lower)} or {
                        person for *_, person in matches
                    }
                    found = found or {
                        person
                        for person, weights[person] in conn.execute(
                            """select n.entity_id, (select count(*) from candidacy y where y.person_id = n.entity_id)
                               from name n join person p on p.id = n.entity_id
                               where n.text = %s and n.is_preferred
                                 and not exists (select from entity_merge m where m.entity_id = n.entity_id)""",
                            [ALIASES.get(candidate, candidate)],
                        ).fetchall()
                    }
                    if found:
                        people[candidate] = max(found, key=lambda person: weights[person])
                    else:
                        people[candidate] = cs.key(f"{SOURCE}.person", f"US:{slug(candidate)}")
                        cs.add("person", id=people[candidate])
                        name(people[candidate], candidate)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{option_key}:head"),
                    person_id=people[candidate],
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=False,
                )
        print(f"{held} caucuses that report only convention delegates wait for the caucus_delegates measure")
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "value": pl.Float64,
                    "measure": pl.Int16,
                },
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
