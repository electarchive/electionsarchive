import html
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "gb.advisory_polls"
VERSION = "2"
VOTES, REGISTERED, BALLOTS = 1, 2, 3
IPSOS = (
    "https://www.ipsos.com/sites/default/files/migrations/en-uk/files/Assets/Docs/Archive/Polls/"
    "scottish-executive-transport.pdf"
)
BBC = "https://web.archive.org/web/20100808062848if_/http://news.bbc.co.uk/2/hi/uk_news/england/manchester/7778110.stm"
HANSARD = "https://api.parliament.uk/historic-hansard/commons/1994/mar/22/engagements"
WIKIPEDIA = "https://en.wikipedia.org/wiki/1994_Strathclyde_water_referendum"
EVALUATION = "https://www.gov.scot/publications/health-board-elections-alternative-pilots-final-report-statutory-evaluation/pages/"
SOURCES = {
    "ipsos": (
        IPSOS,
        ".pdf",
        "academic",
        "Ipsos MORI Scotland for the Scottish Executive Social Research",
        "Evaluation of Edinburgh Residents' Attitudes to the Proposed Road User Charging Scheme (2006)",
    ),
    "bbc": (
        BBC,
        ".html",
        "news_agency",
        "BBC News",
        "Voters reject congestion charge (12 December 2008, archived copy)",
    ),
    "hansard": (
        HANSARD,
        ".html",
        "official_body",
        "UK Parliament",
        "Prime Minister's questions, Engagements, HC Deb 22 March 1994 vol 240 cc131-4",
    ),
    "wikipedia": (
        WIKIPEDIA,
        ".html",
        "compendium",
        "Wikipedia",
        "1994 Strathclyde water referendum",
    ),
    "evaluation": (
        f"{EVALUATION}4/",
        ".html",
        "official_body",
        "Scottish Government",
        "Health Board Elections and Alternative Pilots: Final Report, 3 Selections of Candidates (2012)",
    ),
    "evaluation-introduction": (
        f"{EVALUATION}2/",
        ".html",
        "official_body",
        "Scottish Government",
        "Health Board Elections and Alternative Pilots: Final Report, 1 Introduction (2012)",
    ),
}
HEALTH_DAY = date(2010, 6, 10)
HEALTH_BOARDS = [
    ("fife", "NHS Fife", "S12000015", 12, 60, 39761),
    ("dumfries-and-galloway", "NHS Dumfries and Galloway", "S12000006", 10, 70, 26516),
]
HEALTH_CONTEXT = (
    "One of two pilot elections under the Health Boards (Membership and Elections) (Scotland) Act 2009. Voting was "
    "by post only, by single transferable vote, with the whole board area as one ward, and 16- and 17-year-olds "
    "could vote. The poll closed at 4pm on 10 June 2010. The elected members joined the board's appointed members."
)
HEALTH_CAVEAT = (
    "The Scottish Government's statutory evaluation gives {candidates} candidates, {seats} members elected, and "
    "{ballots:,} ballots returned. No declaration with each candidate's votes or the names of those elected was "
    "found, so only the ballots returned are stored."
)
GM_BOROUGHS = {
    "E08000001": ("Bolton", 20529, 76910),
    "E08000002": ("Bury", 16563, 64001),
    "E08000003": ("Manchester", 43593, 113064),
    "E08000004": ("Oldham", 17571, 68884),
    "E08000005": ("Rochdale", 17333, 61686),
    "E08000006": ("Salford", 14603, 79326),
    "E08000007": ("Stockport", 24090, 103706),
    "E08000008": ("Tameside", 16323, 83105),
    "E08000009": ("Trafford", 20445, 83568),
    "E08000010": ("Wigan", 27810, 78565),
}
POLLS: list[dict[str, Any]] = [
    {
        "key": "edinburgh-2005",
        "from": date(2005, 2, 7),
        "to": date(2005, 2, 21),
        "polity": ("gb.polity", "S12000036"),
        "area": ("gb.lad", "S12000036"),
        "name": "2005 Edinburgh congestion charging referendum",
        "title": "Council transport strategy and congestion charging",
        "path": None,
        "units": [("gb.lad", "S12000036", None, 45965, 133678, 179905, 291228)],
        "primary": "ipsos",
        "cited": ["ipsos"],
        "context": (
            "The City of Edinburgh Council held a postal ballot of its electors on its preferred transport strategy, "
            "which included a road charging scheme with two cordons. The ballot ran for two weeks and closed on "
            "Monday 21 February 2005. The council was not bound by the result under any statute, but dropped the "
            "scheme after voters rejected it."
        ),
        "caveat": (
            "The report prints 291,228 electors eligible to vote and 179,905 ballots returned, and gives no count "
            "of rejected papers. Other published accounts give a different turnout count."
        ),
    },
    {
        "key": "greater-manchester-2008",
        "from": date(2008, 12, 11),
        "to": date(2008, 12, 11),
        "polity": ("gb.polity", "E47000001"),
        "area": ("gb.cauth", "E47000001"),
        "name": "2008 Greater Manchester congestion charge referendum",
        "title": "Transport Innovation Fund bid and peak-time congestion charge",
        "path": None,
        "units": [("gb.lad", code, name, yes, no, None, None) for code, (name, yes, no) in GM_BOROUGHS.items()],
        "primary": "bbc",
        "cited": ["bbc"],
        "context": (
            "The ten Greater Manchester councils, through the Association of Greater Manchester Authorities, polled "
            "their electors by post on a bid for Transport Innovation Fund money that depended on a peak-time "
            "congestion charge. The vote was advisory: the councils said they would go ahead only if a majority "
            "of voters in at least seven of the ten areas agreed. The poll closed on 11 December 2008 and every "
            "borough voted against."
        ),
        "caveat": (
            "Counted by borough from the broadcaster's table, whose Yes and No columns add up to its stated "
            "region-wide totals of 218,860 and 812,815. It prints turnout only as a percentage, so ballots are not "
            "stored."
        ),
    },
    {
        "key": "strathclyde-1994",
        "from": date(1994, 3, 1),
        "to": date(1994, 3, 22),
        "polity": None,
        "area": ("gb.counting_area", "sct-region-1974-strathclyde"),
        "name": "1994 Strathclyde water referendum",
        "title": "Government proposals for Scotland's water and sewerage services",
        "path": "gb/strathclyde-region",
        "units": [("gb.counting_area", "sct-region-1974-strathclyde", None, 33956, 1194667, None, None)],
        "primary": "hansard",
        "cited": ["hansard", "wikipedia"],
        "context": (
            "Strathclyde Regional Council polled its electors by post, with the count run by the Electoral Reform "
            "Society, on the government's plan to move water and sewerage services from the regional councils to "
            "three new water authorities, a step toward privatization. The poll had no legal effect. The exact "
            "polling days were not found, so the dates span the month to the day after the result was announced."
        ),
        "caveat": (
            "The No count is as the House of Commons record gives it (1,194,667, a 71.5 percent return). The Yes "
            "count, 33,956, is from Wikipedia, which cites the Local Government Chronicle and The Herald of March "
            "1994; no official declaration was found."
        ),
    },
]


def fetch() -> None:
    for url, suffix in {(entry[0], entry[1]) for entry in SOURCES.values()}:
        raw.fetch(SOURCE, url, suffix=suffix)


def printed(key: str) -> set[str]:
    url, suffix = SOURCES[key][:2]
    path = raw.cached(SOURCE, url).path
    if suffix == ".pdf":
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode()
    else:
        text = html.unescape(
            re.sub(r"(?s)<(script|style).*?</\1>|<[^>]+>", " ", path.read_bytes().decode("utf-8", errors="replace"))
        )
    return set(re.sub(r"(?<=\d),(?=\d{3}\b)", "", text).split())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Council-run advisory polls: the Strathclyde water referendum of 1994, the Edinburgh congestion "
            "charging referendum of 2005, and the Greater Manchester congestion charge referendum of 2008; and the "
            "ballots returned in the 2010 NHS Fife and NHS Dumfries and Galloway board elections",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        system = cs.key("gb.electoral_system", "referendum")
        if system in cs.minted:
            raise ValueError("publish gb.referendums first")
        sources: dict[str, UUID] = {}
        for key, (url, _, kind, publisher, title) in SOURCES.items():
            sources[key] = cs.source(publisher, title)
            if sources[key] in cs.minted:
                cs.add("source", id=sources[key], kind=kind, publisher=publisher, title=title, url=url)
                cs.artifact(raw.cached(SOURCE, url), sources[key])
        for poll in POLLS:
            day = poll["to"]
            polity = cs.key(*poll["polity"]) if poll["polity"] else cs.polity("GB")
            area = cs.area(*poll["area"])
            if {polity, area} & cs.minted and poll["polity"]:
                raise ValueError(f"{poll['key']}: the council and its area must exist")
            if poll["path"] is None:
                found = conn.execute(
                    "select path from slug where entity_id = %s order by is_current desc limit 1", [polity]
                ).fetchone()
                assert found is not None
                base = found[0]
            else:
                base = poll["path"]
            election = cs.key("gb.election", f"{day}:advisory:{poll['key']}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=None,
                kind="referendum",
                date_from=poll["from"],
                date_to=day,
                status="held",
            )
            name(election, poll["name"])
            election_path = f"{base}/{day}-referendum"
            cs.add("slug", path=election_path, entity_id=election)
            contest = cs.key("gb.contest", f"{day}:advisory:{poll['key']}")
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
            cs.add("ballot_question", contest_id=contest, type="plebiscite")
            name(contest, poll["title"])
            cs.add("slug", path=f"{election_path}/{slug(poll['title'])}", entity_id=contest)
            cs.add("note", entity_id=contest, kind="context", body_md=poll["context"])
            options = [cs.key("gb.advisory_polls.option", f"{poll['key']}:{position}") for position in range(2)]
            for position, (option, label) in enumerate(zip(options, ("Yes", "No"), strict=True)):
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=label,
                )
            result_set = cs.key("gb.advisory_polls.result_set", poll["key"])
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=sources[poll["primary"]],
                is_default=True,
                completeness=1,
            )
            for key in poll["cited"]:
                cs.add("citation", entity_id=result_set, source_id=sources[key])
            cs.add("note", entity_id=result_set, kind="caveat", body_md=poll["caveat"])
            total_yes = sum(unit[3] for unit in poll["units"])
            total_no = sum(unit[4] for unit in poll["units"])
            if total_yes == total_no:
                raise ValueError(f"{poll['key']}: tied")
            cs.add("outcome", result_set_id=result_set, option_id=options[int(total_yes < total_no)], kind="won")
            for scheme, code, label, yes, no, ballots, registered in poll["units"]:
                found = printed(poll["primary"])
                for figure in (yes, no, ballots, registered):
                    if figure is not None and str(figure) not in found | printed(poll["cited"][-1]):
                        raise ValueError(f"{poll['key']} {code}: {figure} is not printed in its sources")
                unit = cs.unit(scheme, code)
                if unit in cs.minted:
                    unit_polity = cs.key("gb.polity", code) if scheme == "gb.lad" else polity
                    unit_name = label
                    if unit_name is None:
                        found = conn.execute(
                            "select text from name where entity_id = %s and is_preferred", [unit_polity]
                        ).fetchone()
                        assert found is not None
                        unit_name = found[0].removeprefix("The ").removesuffix(" Council")
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=unit_polity,
                        scheme_code=scheme,
                        code=code,
                        area_relation="equals",
                        area_id=cs.area(scheme, code),
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, unit_name)
                for option, measure, value in (
                    (options[0], VOTES, yes),
                    (options[1], VOTES, no),
                    (None, BALLOTS, ballots),
                    (None, REGISTERED, registered),
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
                            flags=0,
                            value=value,
                        )

        stv = cs.key("gb.electoral_system", "stv")
        if stv in cs.minted:
            raise ValueError("publish gb.leap_scot first")
        evaluation = printed("evaluation")
        for key, label, code, seats, candidates, ballots in HEALTH_BOARDS:
            if not {str(ballots), str(candidates)} <= evaluation:
                raise ValueError(f"{key}: {ballots} or {candidates} is not printed in the evaluation")
            polity = cs.key("gb.polity", f"health-board:{key}")
            body = cs.key("gb.body", f"health-board:{key}")
            area = cs.area("gb.counting_area", code)
            path = f"gb/{slug(label)}"
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="special_district", iso_code=None)
                cs.add("polity_parent", polity_id=polity, parent_id=cs.polity("GB-SCT"))
                cs.add("polity_territory", polity_id=polity, area_id=area)
                name(polity, label)
                cs.add("slug", path=path, entity_id=polity)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
                name(body, f"{label} Board")
            election = cs.key("gb.election", f"{HEALTH_DAY}:health-board:{key}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                kind="local",
                date_from=HEALTH_DAY,
                date_to=HEALTH_DAY,
                status="held",
            )
            name(election, f"2010 {label} Board election")
            election_path = f"{path}/{HEALTH_DAY}-local"
            cs.add("slug", path=election_path, entity_id=election)
            cs.add("note", entity_id=election, kind="context", body_md=HEALTH_CONTEXT)
            contest = cs.key("gb.contest", f"{HEALTH_DAY}:health-board:{key}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=seats,
                round_number=1,
                electoral_system_id=stv,
                default_result_kind="counted",
            )
            name(contest, f"{label} Board, 2010")
            cs.add("slug", path=f"{election_path}/board", entity_id=contest)
            result_set = cs.key("gb.advisory_polls.result_set", f"health-board:{key}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=sources["evaluation"],
                is_default=True,
                completeness=1,
            )
            cs.add("citation", entity_id=result_set, source_id=sources["evaluation-introduction"])
            cs.add(
                "note",
                entity_id=result_set,
                kind="caveat",
                body_md=HEALTH_CAVEAT.format(candidates=candidates, seats=seats, ballots=ballots),
            )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=cs.unit("gb.counting_area", code),
                option_id=None,
                channel=0,
                measure=BALLOTS,
                count_round=0,
                flags=0,
                value=ballots,
            )
        return cs.write()
