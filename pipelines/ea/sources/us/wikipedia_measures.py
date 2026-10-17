import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wikipedia_measures"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
API = "https://en.wikipedia.org/w/api.php?"
YEARS = (2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2023, 2024, 2025)
ARCHIVES = {"CO", "CT", "ID", "MA", "NH", "NM", "NY", "VT", "VA"}
ALIASES = {"Washington D.C.": "District of Columbia", "U.S. Virgin Islands": "United States Virgin Islands"}
MONTHS = {
    month: index + 1
    for index, month in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))
}
TYPES = {
    "citizens": "initiative",
    "veto referendum": "optional_referendum",
    "advisory question": "plebiscite",
}
DATES = {("MO", "Missouri Amendment 1, State Executive Term Limits Amendment"): (date(2020, 3, 3), date(2020, 11, 3))}
TITLE = "United States ballot measures, by year"
NOTE = (
    "Statewide totals as tabulated in the English Wikipedia's yearly lists of ballot measures, which transcribe "
    "the state canvasses."
)
METHOD = (
    "Statewide Yes and No totals from the English Wikipedia's list of {year} ballot measures. "
    "The outcome is the list's own Approved or Failed mark."
)


def page(year: int) -> str:
    return API + urlencode(
        {
            "action": "parse",
            "page": f"{year} United States ballot measures",
            "prop": "wikitext",
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
        }
    )


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, page(year), suffix=".json")


def parse() -> None:
    with connect() as conn:
        states = {
            text: iso[3:]
            for iso, text in conn.execute(
                """select p.iso_code, n.text from polity p join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code ~ '^US-[A-Z]{2}$' and n.lang = 'en'"""
            ).fetchall()
        }
    rows: list[dict[str, object]] = []
    for year in YEARS:
        text = json.loads(raw.cached(SOURCE, page(year)).path.read_text())["parse"]["wikitext"]
        text = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
        text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text).replace("{{nbsp}}", " ")
        for section in re.split(r"^===(?!=)\s*", text, flags=re.MULTILINE)[1:]:
            heading, _, body = section.partition("\n")
            label = heading.strip(" =")
            state = states.get(ALIASES.get(label, label))
            if state is None or state in ARCHIVES:
                print(f"{year}: skipped {label}")
                continue
            for chunk in re.split(r"^\|-\s*$", body, flags=re.MULTILINE):
                cells: list[str] = []
                for line in chunk.split("\n"):
                    if line.startswith(("{|", "|}", "!")):
                        continue
                    if line.startswith("|"):
                        cell = line[1:]
                        if re.match(r"\s*(style|rowspan|colspan)=", cell):
                            cell = cell.split("|", 1)[1] if "|" in cell else ""
                        cells.append(cell.strip())
                    elif cells:
                        cells[-1] += " " + line.strip()
                if cells and re.sub(r"\s+", " ", cells[0]) == label:
                    cells = cells[1:]
                if "% req." in body and len(cells) >= 8:
                    del cells[5]
                if len(cells) < 7:
                    continue
                origin, status, measure, _, when, yes, no = cells[:7]
                found = re.match(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2})", when)
                votes = [re.search(r"\d[\d,]*", cell.replace("'", "")) for cell in (yes, no)]
                if found is None or None in votes:
                    print(
                        f"{year} {state}: no date or votes for {measure[:60]!r} ({when!r}, {yes[:20]!r}, {no[:20]!r})"
                    )
                    continue
                named = next((text for text in states if measure.startswith(f"{text} ")), None)
                measure = re.sub(r"\s+", " ", re.sub(r"'{2,}", "", measure)).strip()
                day = date(year, MONTHS[found[1]], int(found[2]))
                printed, corrected = DATES.get((state, measure), (None, day))
                rows.append(
                    {
                        "state": states[named] if named is not None and named != label else state,
                        "day": corrected if day == printed else day,
                        "origin": re.sub(r"\s+", " ", origin).strip().lower(),
                        "status": re.sub(r"[^A-Za-z]", "", re.split(r"<br", status)[0]).replace("Passed", "Approved"),
                        "measure": measure,
                        "yes": int(votes[0][0].replace(",", "")),
                        "no": int(votes[1][0].replace(",", "")),
                    }
                )
    frame = pl.DataFrame(rows)
    unknown = frame.filter(~pl.col("status").is_in(["Approved", "Failed"]))
    if unknown.height:
        raise ValueError(f"unknown statuses: {unknown.select('state', 'measure', 'status').rows()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.unique(["state", "day", "measure"], keep="first", maintain_order=True).write_parquet(
        PARSED / "measures.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot questions by state, 2012–2025, from the English Wikipedia's yearly lists",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/2016_United_States_ballot_measures",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, page(year)), source)
        system = cs.key("us.electoral_system", "referendum")
        names = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p join name n on n.entity_id = p.id
                   and n.is_preferred and n.lang = 'en' where p.iso_code ~ '^US-[A-Z]{2}$'"""
            ).fetchall()
        )
        states = {
            abbreviation: (area, code)
            for abbreviation, area, code in conn.execute(
                """select substr(p.iso_code, 4), u.area_id, u.code from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        cs.renamed.update({"2019-10-12:LA:": "2019-10-12:KS:"})
        misfiled, louisiana = cs.key("us.election", "2019-10-12:KS:referendum"), cs.key("us.election", "2019-10-12:LA")
        if misfiled not in cs.minted and misfiled != louisiana:
            cs.merge(misfiled, louisiana)
        paths = dict(
            conn.execute(
                """select x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
                   where x.scheme = 'us.election'"""
            ).fetchall()
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        held = dict(
            conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        covered = set(
            conn.execute(
                """select substr(p.iso_code, 4), e.date_from from contest k
                   join ballot_question q on q.contest_id = k.id
                   join election e on e.id = k.election_id
                   join polity p on p.id = e.polity_id
                   join changeset c on c.id = k.changeset_id
                   where c.pipeline <> %s and p.iso_code ~ '^US-[A-Z]{2}$'""",
                [SOURCE],
            ).fetchall()
        )
        retained = {
            id
            for (id,) in conn.execute(
                """select rs.id from result_set rs join external_id x on x.entity_id = rs.id and x.scheme = %s
                   where not (rs.status = 'active' and rs.is_default)""",
                [f"{SOURCE}.result_set"],
            ).fetchall()
        }
        created: set[str] = set()
        for state, day, origin, status, measure, yes, no in frame.sort("state", "day").iter_rows():
            if day.year == 2024 and (state, day) in covered:
                continue
            if state not in states:
                print(f"no state area for {state}, skipped {measure}")
                continue
            if f"{day}:{state}:referendum" in known:
                election_key, kind = f"{day}:{state}:referendum", "referendum"
            elif f"{day}:{state}:primary" in known:
                election_key, kind = f"{day}:{state}:primary", "primary"
            elif f"{day}:{state}" in known or day.month == 11:
                election_key, kind = f"{day}:{state}", "general"
            else:
                election_key, kind = f"{day}:{state}:referendum", "referendum"
            election = cs.key("us.election", election_key)
            if election_key not in known and election_key not in created:
                created.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=cs.key("us.election", str(day)) if str(day) in known else None,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    election,
                    f"{day.year} {names[state]} {kind} election"
                    if kind != "referendum"
                    else f"{names[state]} referendum of {day:%B} {day.day}, {day.year}",
                )
                paths[election_key] = f"us/{state.lower()}/{day}-{kind}"
                cs.add("slug", path=paths[election_key], entity_id=election)
            contest_key = f"{DATES.get((state, measure), (day,))[0]}:{state}:question:wikipedia:{slug(measure)[:80]}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=states[state][0],
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type=TYPES.get(origin, "mandatory_referendum"))
            name(contest, measure)
            path = held.get(contest)
            if path is None or not path.startswith(f"{paths[election_key]}/"):
                base = f"{paths[election_key]}/{slug(measure)[:60].rstrip('-') or 'question'}"
                path, n = base, 1
                while path in taken:
                    n += 1
                    path = f"{base}-{n}"
                taken.add(path)
            cs.add("slug", path=path, entity_id=contest)
            options = {}
            for position, label in enumerate(("Yes", "No")):
                options[label] = cs.key("us.option", f"{contest_key}:{label.lower()}")
                cs.add(
                    "contest_option",
                    id=options[label],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=label,
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            if result_set in retained:
                continue
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
                completeness=1,
            )
            if result_set in cs.minted:
                for label, votes in (("Yes", yes), ("No", no)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.state", states[state][1]),
                        option_id=options[label],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD.format(year=day.year))
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=options["Yes" if status == "Approved" else "No"],
                kind="won",
            )
        return cs.write()
