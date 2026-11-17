"""The 2003 Czech referendum on accession to the European Union, by polling district, from the volby.cz pages.

The referendum was held on 13 and 14 June 2003 under Constitutional Act 515/2002. volby.cz prints the results for the
country, each region (kraj), each district (okres), each municipality (obec), and each polling district (okrsek). The
package, `cz.volby/ref-2003`, holds one ballot question with its two answers and a count for every polling district.
It is built on `cz.volby/geo`.
"""

import re
from collections import Counter
from datetime import date

from ea import package, raw
from ea.package import Package
from ea.sources.cz.volby import (
    BASE,
    CZECHIA,
    SOURCE,
    TERRITORY,
    _accounting,
    _overcount,
    _places,
    area,
    key,
)
from ea.sources.cz.volby_ps import rows, whole

FIRST, LAST = date(2003, 6, 13), date(2003, 6, 14)
PAGES = f"{BASE}/pls/ref2003"
WORKERS = 12
REF_SOURCE = key("source", "cz.volby.ref2003")
SYSTEM = key("electoral_system", "referendum", 2003)
ELECTION = key("election", "ref", 2003)
CONTEST = key("contest", "ref", 2003)
RESULT_SET = key("result_set", "ref", 2003)
YES, NO = key("option", "ref", 2003, "yes"), key("option", "ref", 2003, "no")
VOTES = 1
TITLE = "Referendum o přistoupení České republiky k Evropské unii"
QUESTION = "Souhlasíte s tím, aby se Česká republika stala členským státem Evropské unie?"
QUESTION_EN = "Do you agree that the Czech Republic should become a member state of the European Union?"
RULES: dict[str, object] = {"formula": "referendum", "quorum": 0.5}
SYSTEM_METHOD = (
    "Constitutional Act 515/2002 on the referendum on the accession of the Czech Republic to the European Union set "
    "the rules of this one vote. The proposal was adopted if more than half of the voters who took part voted for "
    "it and more than half of all registered voters took part."
)
METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as printed on the volby.cz results "
    "pages. Registered voters are the electoral roll, voters are the envelopes issued, ballots cast are the "
    "envelopes put in the box, and valid votes are the valid votes for either answer. Polling districts are placed "
    "in their municipality; where the municipality's code no longer exists, in its district."
)


def link(name: str, **query: object) -> str:
    tail = "&".join(f"{k}={v}" for k, v in {"xjazyk": "CZ", **query}.items())
    return f"{PAGES}/{name}?{tail}"


def read(name: str, **query: object) -> str:
    return raw.cached(SOURCE, link(name, **query)).path.read_text(encoding="utf-8")


def _kraje() -> list[str]:
    return re.findall(r"re1211\?xjazyk=CZ&xnumnuts=(\d+)&xnumnuts1=0", read("re12"))


def _okresy() -> list[tuple[str, str, str]]:
    """Each district as (region number, district number, district code)."""
    found: list[tuple[str, str, str]] = []
    for k in _kraje():
        text = read("re1211", xnumnuts=k, xnumnuts1=0)
        found += [
            (k, o, code)
            for o, code in re.findall(rf"xnumnuts=(\d+)&xnumnuts1={k}&xnumnuts2=0\">\s*(CZ\w+)\s*</a>", text)
        ]
    return found


def _obce() -> list[tuple[str, str, str, str]]:
    found: list[tuple[str, str, str, str]] = []
    for k, o, _ in _okresy():
        text = read("re1211", xnumnuts=o, xnumnuts1=k, xnumnuts2=0)
        found += [
            (k, o, c, t)
            for c, t in re.findall(
                rf"re1111\?xjazyk=CZ&xnumnuts={o}&xnumnuts1={k}&xnumnuts2=0&xobec=(\d+)&xtyp=(\d+)", text
            )
        ]
    return found


def fetch() -> None:
    for name in ("re13", "re12", "re3", "re4"):
        raw.fetch(SOURCE, link(name), suffix=".html")
    raw.fetch_many(SOURCE, [link("re1211", xnumnuts=k, xnumnuts1=0) for k in _kraje()], suffix=".html", workers=WORKERS)
    raw.fetch_many(
        SOURCE,
        [link("re1211", xnumnuts=o, xnumnuts1=k, xnumnuts2=0) for k, o, _ in _okresy()],
        suffix=".html",
        workers=WORKERS,
    )
    raw.fetch_many(
        SOURCE,
        [link("re1111", xnumnuts=o, xnumnuts1=k, xnumnuts2=0, xobec=c, xtyp=t) for k, o, c, t in _obce()],
        suffix=".html",
        workers=WORKERS,
    )


def packages() -> list[str]:
    return ["cz.volby/ref-2003"]


def parse() -> None:
    referendum().write()


def emit() -> None:
    package.run("load", packages())


def _figures(cells: list[str]) -> dict[int, int]:
    """Registered, issued, cast, valid, yes (measure 1), and no (measure 0) from the cells after a row's label."""
    picked = (cells[0], cells[1], cells[3], cells[4], cells[5], cells[6])
    return dict(zip((2, 14, 3, 4, VOTES, 0), map(whole, picked), strict=True))


def national() -> dict[int, int]:
    (row,) = [r for r in rows(read("re13")) if len(r) == 10 and r[0].replace(" ", "").isdigit()]
    return _figures(row[3:])


def referendum() -> Package:
    p = Package(
        "cz.volby/ref-2003",
        SOURCE,
        "The 2003 Czech referendum on accession to the European Union by polling district from the volby.cz "
        "results pages",
        depends=["cz.volby/geo"],
    )
    p.external |= {CZECHIA, TERRITORY}
    p.add(
        "source",
        id=REF_SOURCE,
        kind="official_body",
        publisher="Český statistický úřad",
        title="Referendum o přistoupení České republiky k Evropské unii, 13.-14.06.2003: results pages",
        url="https://volby.gov.cz/pls/ref2003/re?xjazyk=CZ",
        license="ČSÚ open data terms: free reuse with the source acknowledged",
        license_url="https://volby.gov.cz/opendata/opendata.htm",
        attribution="Český statistický úřad, volby.cz",
    )
    places = _places()
    towns = set(places.filter(places["scheme_code"] == "cz.obec")["code"])
    districts = set(places.filter(places["scheme_code"] == "cz.okres")["code"])
    for name in ("re13", "re12", "re3", "re4"):
        p.artifact(raw.cached(SOURCE, link(name)), REF_SOURCE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=CZECHIA,
        valid_from=date(2003, 1, 1),
        valid_to=date(2004, 1, 1),
        rules=RULES,
        rules_version=1,
    )
    p.name(SYSTEM, "Referendum on accession to the European Union", "en", "official")
    p.name(SYSTEM, "Referendum o přistoupení k Evropské unii", "cs", "native", False)
    p.add("note", id=p.id("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_METHOD, lang="en")
    p.add("election", id=ELECTION, polity_id=CZECHIA, kind="referendum", date_from=FIRST, date_to=LAST, status="held")
    p.name(ELECTION, "2003 Czech European Union accession referendum", "en", "official")
    p.name(ELECTION, TITLE, "cs", "native", False)
    path = f"cz/{FIRST}-referendum"
    p.add("slug", path=path, entity_id=ELECTION, is_current=True)
    p.add(
        "contest",
        id=CONTEST,
        election_id=ELECTION,
        kind="ballot_question",
        district_area_id=TERRITORY,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=CONTEST, type="referendum")
    p.name(CONTEST, "Accession of the Czech Republic to the European Union", "en", "official")
    p.name(CONTEST, TITLE, "cs", "native", False)
    p.add("slug", path=f"{path}/accession", entity_id=CONTEST, is_current=True)
    p.add(
        "note",
        id=key("note", CONTEST, "question"),
        entity_id=CONTEST,
        kind="methodology",
        body_md=f"The question on the ballot: {QUESTION_EN} (Czech: {QUESTION})",
        lang="en",
    )
    p.add(
        "result_set",
        id=RESULT_SET,
        election_id=ELECTION,
        contest_id=CONTEST,
        stage="official",
        kind="counted",
        source_id=REF_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", RESULT_SET), entity_id=RESULT_SET, kind="methodology", body_md=METHOD, lang="en")
    for option, label, position in ((YES, "Yes", 1), (NO, "No", 2)):
        p.add(
            "contest_option",
            id=option,
            contest_id=CONTEST,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )
    printed = national()
    for measure, value in printed.items():
        if measure == VOTES:
            p.add("declared", result_set_id=RESULT_SET, option_id=YES, measure=VOTES, value=value)
        elif measure == 0:
            p.add("declared", result_set_id=RESULT_SET, option_id=NO, measure=VOTES, value=value)
        else:
            p.add("declared", result_set_id=RESULT_SET, option_id=None, measure=measure, value=value)
    if printed[VOTES] > printed[0]:
        p.add("outcome", result_set_id=RESULT_SET, option_id=YES, kind="won")
        p.add("outcome", result_set_id=RESULT_SET, option_id=NO, kind="lost")
    else:
        p.add("outcome", result_set_id=RESULT_SET, option_id=NO, kind="won")
        p.add("outcome", result_set_id=RESULT_SET, option_id=YES, kind="lost")

    district_code = {o: code for _, o, code in _okresy()}
    totals: Counter[int] = Counter()
    pairs: list[tuple[str, dict[str, str | None]]] = []
    for k, o, c, t in _obce():
        artifact = raw.cached(SOURCE, link("re1111", xnumnuts=o, xnumnuts1=k, xnumnuts2=0, xobec=c, xtyp=t))
        p.artifact(artifact, REF_SOURCE)
        text = artifact.path.read_text(encoding="utf-8")
        nuts = district_code.get(o)
        if c in towns:
            place = area("cz.obec", c)
        elif nuts in districts:
            place = area("cz.okres", str(nuts))
        else:
            place = TERRITORY
        for cells in rows(text):
            if len(cells) != 8 or not cells[0].isdigit():
                continue
            number, registered, issued, _, cast, valid, yes, no = cells
            code = f"{FIRST}:{c}:{int(number)}"
            unit = key("unit", code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CZECHIA,
                scheme_code="cz.okrsek",
                code=code,
                area_relation="within",
                area_id=place,
                kind="standard",
            )
            row: dict[str, str | None] = {
                "VOL_SEZNAM": str(whole(registered)),
                "VYD_OBALKY": str(whole(issued)),
                "ODEVZ_OBAL": str(whole(cast)),
                "PL_HL_CELK": str(whole(valid)),
            }
            pairs.append((RESULT_SET, row))
            _accounting(p, RESULT_SET, unit, row)
            for option, value in ((YES, whole(yes)), (NO, whole(no))):
                p.add(
                    "result_value",
                    result_set_id=RESULT_SET,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=value,
                )
            for measure, value in zip(
                (2, 14, 3, 4, VOTES, 0), map(whole, (registered, issued, cast, valid, yes, no)), strict=True
            ):
                totals[measure] += value
    differ = {m: (totals[m], printed[m]) for m in printed if totals[m] != printed[m]}
    if differ:
        raise ValueError(f"polling districts do not sum to the printed national total: {differ}")
    _overcount(p, pairs)
    return p
