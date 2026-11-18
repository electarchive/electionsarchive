"""Moldova's local referendums, mostly recalls of a mayor, from the Central Electoral Commission's report on each
referendum or the scanned protocol of the circumscription's electoral council. Each locality's figures come from one
document, so each package holds one unit per referendum with its roll, turnout, invalid ballots, and the votes for each
answer."""

from dataclasses import dataclass, field
from datetime import date

from ea import raw
from ea.package import Package
from ea.sources.md import cec, local

SOURCE = "md.cec"
MOLDOVA = local.MOLDOVA
OLD = "https://a.cec.md/storage/old_site_files"
NEW = "https://a.cec.md/storage/ckfinder/files"
REGISTERED, VOTES, CAST, VALID, INVALID, RECEIVED = 2, 1, 3, 4, 5, 14


@dataclass
class Vote:
    """One referendum: where, what was asked, and what the document prints."""

    district: str
    locality: str | None  # None for the district-level municipality
    place: str  # the name for titles
    place_ro: str
    recall: str | None  # the mayor's office the question recalls, or None
    registered: int
    cast: int
    yes: int
    no: int
    invalid: int | None
    received: int | None = None
    question: str | None = None
    note: str | None = None
    page: int = 1


@dataclass
class Day:
    day: date
    url: str
    suffix: str
    votes: list[Vote] = field(default_factory=list)
    totals: tuple[int, int, int, int] | None = None  # printed registered, cast, valid, invalid


DAYS: dict[str, Day] = {
    "2013-07-07": Day(
        date(2013, 7, 7),
        f"{OLD}//files/files/8869_6682.pdf",
        ".pdf",
        [
            Vote(
                "cahul",
                "lebedenco",
                "Lebedenco",
                "Lebedenco",
                "mayor",
                2021,
                512,
                454,
                58,
                None,
                note="The court's ruling prints the roll including the supplementary lists, the participants, and the "
                "votes for and against, and no figure for invalid ballots.",
            )
        ],
    ),
    "2013-12-29": Day(
        date(2013, 12, 29),
        f"{OLD}/files/7715_raport_referendum.pdf",
        ".pdf",
        [
            Vote(
                "anenii noi",
                "anenii noi",
                "Anenii Noi",
                "Anenii Noi",
                "mayor",
                10570,
                1535,
                1271,
                253,
                11,
                note="The roll is 10,544 voters on the basic lists and 26 on the supplementary lists.",
                page=1,
            )
        ],
    ),
    "2014-05-11": Day(
        date(2014, 5, 11),
        f"{OLD}/files/4304_raport_totalizare.pdf",
        ".pdf",
        [
            Vote(
                "leova",
                "cneazevca",
                "Cneazevca",
                "Cneazevca",
                "mayor",
                943,
                78,
                52,
                26,
                0,
                note="The roll is 931 voters on the basic lists and 12 on the supplementary lists.",
                page=2,
            )
        ],
    ),
    "2016-10-30": Day(
        date(2016, 10, 30),
        f"{OLD}//files/files/Alegeri_prezidentiale/Calarasi_Pitusca_procese-verbale.pdf",
        ".pdf",
        [
            Vote(
                "calarasi",
                "pitusca",
                "Pitușca",
                "Pitușca",
                "mayor",
                2724,
                1150,
                449,
                670,
                31,
                received=1150,
                note="The roll is 2,718 voters on the basic lists and 6 on the supplementary lists.",
            )
        ],
    ),
    "2017-11-19": Day(
        date(2017, 11, 19),
        f"{OLD}//files/files/REFERENDUM/Referendum_Chisinau_19_noiembrie_2017/RAPORT_RL_Chisinau_19_11_2017.pdf",
        ".pdf",
        [
            Vote(
                "chisinau",
                None,
                "Chișinău municipality",
                "municipiul Chișinău",
                "mayor",
                631508,
                108695,
                96465,
                11112,
                1118,
                note="The roll is 629,684 voters on the basic lists and 1,824 on the supplementary lists.",
                page=6,
            )
        ],
    ),
    "2026-05-17": Day(
        date(2026, 5, 17),
        f"{NEW}/ALN%202024%20-%202027/ALN%2017.05.2026/C2_Raport_ALN_17%20mai%202026.pdf",
        ".pdf",
        [
            Vote(
                "drochia",
                "hasnasenii mari",
                "Hăsnășenii Mari",
                "Hăsnășenii Mari",
                "mayor",
                1224,
                494,
                340,
                148,
                6,
                page=88,
            ),
            Vote("cantemir", "sadic", "Sadîc", "Sadîc", "mayor", 1746, 414, 373, 34, 7, page=88),
        ],
        totals=(2970, 908, 895, 13),
    ),
    "2026-06-21": Day(
        date(2026, 6, 21),
        f"{NEW}/ALN%202024%20-%202027/Referendum%2021.06.2026/C2_Raport_Referendum%20Co%C8%99ni%C8%9Ba_21%20iunie%202026.pdf",
        ".pdf",
        [
            Vote(
                "dubasari",
                "cosnita",
                "Coșnița",
                "Coșnița",
                None,
                5134,
                1544,
                1466,
                66,
                12,
                question="Local referendum on mineral resource extraction near the villages of Coșnița and Pohrebea",
                page=34,
            )
        ],
        totals=(5134, 1544, 1532, 12),
    ),
    "2026-11-01": Day(
        date(2026, 11, 1),
        f"{NEW}/ALN%20%C8%99i%20referendum%20local%201%20nov%202026/4_10_Anenii%20Noi_Delac%C4%83u_PV_referendum.pdf",
        ".pdf",
        [
            Vote(
                "anenii noi",
                "delacau",
                "Delacău",
                "Delacău",
                None,
                1724,
                606,
                568,
                23,
                15,
                received=607,
                question="Do you support banning the change of use of land within the administrative limits of the "
                "village of Delacău for the extraction of mineral resources less than 1 km from the perimeter of "
                "the village's built-up area?",
                note="The roll is 1,723 voters on the basic lists and 1 on the supplementary list.",
            )
        ],
    ),
}
REGISTRY = "md.cec/registry"


def key(kind: str, *parts: object) -> str:
    return local.key(f"local-referendum:{kind}", *parts)


def packages() -> list[str]:
    return [f"md.cec/local-referendum-{day}" for day in DAYS]


def fetch() -> None:
    for day in DAYS.values():
        raw.fetch(SOURCE, day.url, suffix=day.suffix)


def area(vote: Vote) -> str:
    if vote.locality is None:
        return local.district_area(vote.district)
    return local.locality_area(vote.district, vote.locality)


def build(name: str) -> Package:
    day = DAYS[name]
    p = Package(
        f"md.cec/local-referendum-{name}",
        SOURCE,
        f"The local referendums of {day.day}: the roll, turnout, invalid ballots, and the votes for each answer in "
        "each locality, from the Central Electoral Commission's own document on it",
        depends=[local.GEO, local.REGISTRY, REGISTRY],
    )
    p.external.add(MOLDOVA)
    p.uses(raw.cached(SOURCE, day.url))
    election = key("election", name)
    many = len(day.votes) > 1
    p.add(
        "election", id=election, polity_id=MOLDOVA, kind="referendum", date_from=day.day, date_to=day.day, status="held"
    )
    p.add("slug", path=f"md/{name}-local-referendum", entity_id=election, is_current=True)
    p.name(election, f"Moldovan local referendum{'s' if many else ''} of {name}", "en", "official")
    p.name(election, f"Referendum{'uri' if many else ''} local{'e' if many else ''} din {name}", "ro", "native", False)
    for vote in day.votes:
        label = f"{vote.district}:{vote.locality or ''}:{name}"
        contest = key("contest", label)
        recall = vote.recall is not None
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=area(vote),
            round_number=1,
            electoral_system_id=cec.SYSTEM_REFERENDUM,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="recall_question" if recall else "referendum")
        english = f"Referendum on recalling the mayor of {vote.place}" if recall else f"{vote.place} local referendum"
        romanian = (
            f"Referendum local privind revocarea primarului {vote.place_ro}"
            if recall
            else f"Referendum local {vote.place_ro}"
        )
        p.name(contest, english, "en", "official")
        p.name(contest, romanian, "ro", "native", False)
        slug_place = local.slug(vote.locality or vote.district)
        p.add(
            "slug",
            path=f"md/{name}-local-referendum/{vote.district.replace(' ', '-')}-{slug_place}",
            entity_id=contest,
            is_current=True,
        )
        if vote.question:
            p.add(
                "note",
                id=key("note", contest, "question"),
                entity_id=contest,
                kind="caveat",
                body_md=vote.question,
                lang="en",
            )
        result_set = key("result_set", label)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=cec.CEC_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = (
            "Counted for the whole locality and published by the Central Electoral Commission. Registered voters are "
            "the voters on the basic roll plus those on the supplementary roll, and ballots cast are the voters who "
            "took part."
        )
        if vote.note:
            method += " " + vote.note
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        unit = key("unit", label)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MOLDOVA,
            scheme_code="md.locality",
            code=f"referendum:{name}:{vote.district}:{vote.locality or ''}",
            area_relation="equals",
            area_id=area(vote),
            kind="standard",
        )
        p.name(unit, f"{vote.place} ({name})", "en", "official")
        options: list[tuple[str, int]] = []
        for position, (text, count, word) in enumerate((("Yes", vote.yes, "yes"), ("No", vote.no, "no")), 1):
            option = key("option", label, word)
            options.append((option, count))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=text,
            )
        valid = vote.yes + vote.no
        rows: list[tuple[str | None, int, int | None]] = [
            (None, REGISTERED, vote.registered),
            (None, CAST, vote.cast),
            (None, VALID, valid),
            (None, INVALID, vote.invalid),
            (None, RECEIVED, vote.received),
            *((option, VOTES, count) for option, count in options),
        ]
        for option, measure, amount in rows:
            if amount is None:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=amount,
            )
        p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=vote.registered)
        p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=vote.cast)
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=valid)
        if vote.invalid is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=vote.invalid)
        for option, count in options:
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=count)
    return p


def totals() -> None:
    """Compare each package's sums with the totals its document prints."""
    for name, day in DAYS.items():
        if day.totals is None:
            continue
        got = (
            sum(v.registered for v in day.votes),
            sum(v.cast for v in day.votes),
            sum(v.yes + v.no for v in day.votes),
            sum(v.invalid or 0 for v in day.votes),
        )
        if got != day.totals:
            raise ValueError(f"{name}: the report prints {day.totals} and the rows add up to {got}")


def parse() -> None:
    totals()
    for name in DAYS:
        build(name).write()
