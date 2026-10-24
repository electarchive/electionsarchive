from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.ep_ni"
VERSION = "1"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED, QUOTA = 1, 2, 3, 4, 5, 21, 26
DAY = date(2019, 5, 23)
DECLARATION = "https://cain.ulster.ac.uk/issues/politics/election/2019eu/2019-05-27_EONI_EU-final.pdf"
DECLARATION_TITLE = "European Parliamentary election, 23 May 2019, Electoral Region of Northern Ireland: result"
CAIN = "https://cain.ulster.ac.uk/issues/politics/election/re2019.htm"
CAIN_TITLE = "European Election (NI) Thursday 23 May 2019"
TOTALS = {REGISTERED: 1278951, BALLOTS: 577275, VALID: 572447, INVALID: 4649, QUOTA: 143112}
CANDIDATES = (
    ("Jim Allister", "Traditional Unionist Voice - TUV", "Q3499853", (62021, 63872, 79540, 89854, 90079), "lost"),
    ("Martina Anderson", "Sinn Féin", "Q4992676", (126951, 128117, 128190, 128200.5, 152436.5), "elected"),
    ("Clare Bailey", "Green Party Northern Ireland", "Q24052779", (12471,), "eliminated"),
    ("Amandeep Singh Bhogal", "Conservative and Unionist Party", "Q63854077", (662,), "eliminated"),
    (
        "Diane Dodds",
        "Democratic Unionist Party - D.U.P.",
        "Q116719",
        (124991, 127291, 155422, 143112, 143112),
        "elected",
    ),
    (
        "Colum Eastwood",
        "SDLP (Social Democratic & Labour Party)",
        "Q5149523",
        (78589, 80949, 82101, 82413.5),
        "eliminated",
    ),
    ("Robert Hill", "UKIP – Unionists For Brexit", None, (5115,), "eliminated"),
    ("Danny Kennedy", "Ulster Unionist Party", "Q5220512", (53052, 54736), "eliminated"),
    ("Naomi Long", "Alliance Party", "Q695272", (105928, 115327, 122263, 123917, 170370), "elected"),
    ("Neil McCann", "Independent", "Q63854193", (948,), "eliminated"),
    ("Jane Morrice", "Independent", "Q6152576", (1719,), "eliminated"),
)
PARTIES = {
    "Traditional Unionist Voice - TUV": "Traditional Unionist Voice",
    "Sinn Féin": "Sinn Féin",
    "Green Party Northern Ireland": "Green Party Northern Ireland",
    "Democratic Unionist Party - D.U.P.": "Democratic Unionist Party",
    "SDLP (Social Democratic & Labour Party)": "Social Democratic and Labour Party",
    "UKIP – Unionists For Brexit": "UK Independence Party",
    "Ulster Unionist Party": "Ulster Unionist Party",
    "Alliance Party": "Alliance Party of Northern Ireland",
}
NONTRANSFERABLE = (0, 2155, 4931, 4950, 12625)
CAVEATS = (
    (
        "The Electoral Office's declaration prints 4,649 invalid votes, but its votes polled less its valid votes give 4,828. "
        "Both printed figures are stored as published."
    ),
    (
        "The Electoral Office's declaration gives the first stage and the last; stages 2 to 4 are from CAIN's transcription of "
        "the count, which agrees with the declaration at both ends."
    ),
    (
        "The fifth stage sums to 568,622.5, not 572,447: it distributed only Colum Eastwood's 78,589 first preferences, and "
        "the 3,824.5 votes he had received by transfer were left undistributed once the last two seats were filled. Every "
        "stage is stored as published."
    ),
)


def fetch() -> None:
    raw.fetch(SOURCE, DECLARATION, suffix=".pdf")
    raw.fetch(SOURCE, CAIN, suffix=".html")


def emit() -> Path:
    for _, _, _, series, _ in CANDIDATES:
        if len(series) > len(NONTRANSFERABLE):
            raise ValueError("a candidate has more stages than the count")
    for stage in range(4):
        total = sum(series[stage] for *_, series, _ in CANDIDATES if len(series) > stage) + NONTRANSFERABLE[stage]
        if total != TOTALS[VALID]:
            raise ValueError(f"stage {stage + 1} sums to {total}")
    if sum(series[0] for *_, series, _ in CANDIDATES) != TOTALS[VALID]:
        raise ValueError("first preferences do not sum to the valid votes")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Ireland's 2019 European Parliament election by candidate, stage by stage, from the "
            "Electoral Office's declaration",
            polity="GB",
        )
        unit = cs.unit("gb.ep_constituency", "northern-ireland")
        if unit in cs.minted:
            raise ValueError("no gb.ep_constituency northern-ireland unit")
        (contest, election), *more = conn.execute(
            """select distinct c.id, c.election_id from contest c join election e on e.id = c.election_id
               join result_set r on r.contest_id = c.id
               join lateral (select v.unit_k from result_value v where v.result_set_k = r.k limit 1) v on true
               join reporting_unit u on u.k = v.unit_k
               where e.date_from = %s and u.id = %s""",
            [DAY, unit],
        ).fetchall()
        if more:
            raise ValueError("more than one Northern Ireland contest in 2019")
        declaration = cs.source("Electoral Office for Northern Ireland", DECLARATION_TITLE)
        cs.add(
            "source",
            id=declaration,
            kind="official_body",
            publisher="Electoral Office for Northern Ireland",
            title=DECLARATION_TITLE,
            url=DECLARATION,
            license="Open Government Licence v3.0",
            license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            attribution="Result sheet of the Chief Electoral Officer for Northern Ireland, under the Open Government "
            "Licence v3.0, as kept by CAIN at Ulster University.",
            notes="A scanned photograph of the result sheet, with the first and fifth stages.",
        )
        cs.artifact(raw.cached(SOURCE, DECLARATION), declaration)
        cain = cs.source("CAIN, Ulster University", CAIN_TITLE)
        cs.add(
            "source",
            id=cain,
            kind="academic",
            publisher="CAIN, Ulster University",
            title=CAIN_TITLE,
            url=CAIN,
            notes="Transcribes all five stages of the count.",
        )
        cs.artifact(raw.cached(SOURCE, CAIN), cain)
        result_set = cs.key(f"{SOURCE}.result_set", "2019:northern-ireland")
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=declaration,
            is_default=True,
            completeness=1,
        )
        cs.add("citation", entity_id=result_set, source_id=cain)
        for text in CAVEATS:
            cs.add("note", entity_id=result_set, kind="caveat", body_md=text)

        def value(option: object, measure: int, stage: int, amount: float) -> None:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=stage,
                flags=0,
                value=amount,
            )

        for measure, amount in TOTALS.items():
            value(None, measure, 0, amount)
        for stage, amount in enumerate(NONTRANSFERABLE[1:], start=2):
            value(None, EXHAUSTED, stage, amount)
        parties = {
            text: party
            for text, party in conn.execute(
                """select distinct on (n.text) n.text, o.party_id from contest_option o
                   join name n on n.entity_id = o.party_id and n.is_preferred where o.contest_id = %s""",
                [contest],
            ).fetchall()
        }
        parties["Conservative and Unionist Party"] = cs.key("wikidata", "Q9626")
        for number, (label, description, item, series, kind) in enumerate(CANDIDATES, start=1):
            party = None if description == "Independent" else parties[PARTIES.get(description, description)]
            if item is not None:
                person = cs.key("wikidata", item)
            else:
                person = cs.key(f"{SOURCE}.person", f"2019:{number}")
            if person in cs.minted:
                cs.add("person", id=person)
                cs.add("name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True)
            option = cs.key(f"{SOURCE}.option", f"2019:{number}")
            cs.add(
                "contest_option", id=option, contest_id=contest, kind="candidate", party_id=party, ballot_label=label
            )
            candidacy = cs.key(f"{SOURCE}.candidacy", f"2019:{number}")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=party,
                is_independent=party is None,
                is_incumbent=label in ("Martina Anderson", "Diane Dodds"),
            )
            for stage, amount in enumerate(series, start=1):
                value(option, VOTES, stage, amount)
            cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        return cs.write()
