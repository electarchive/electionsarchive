"""Algeria's eleven national referendums, 1962 to 2020, from the proclamations printed in the Journal officiel.

Each vote's result was proclaimed by a commission or by the Constitutional Council and printed in the French edition
of the Journal officiel de la République algérienne (JORADP) a few days later. The issues before 2020 are scans, so
the tables are transcribed into `referendum_tables`; the parse checks each one against the totals the proclamation prints.
"""

from collections.abc import Sequence
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.dz import referendum_tables as tables

SOURCE = "dz.referendums"
ALGERIA = str(uuid5(package.NAMESPACE, "polity\x1fDZ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fDZA"))
BASE = "https://www.joradp.dz/FTP/JO-FRANCAIS/"
LICENSE = "No license stated: official government record; facts published with attribution"
PUBLISHER = "Secrétariat général du Gouvernement (Journal officiel de la République algérienne)"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
ADDED_1984 = date(1984, 2, 4)
CREATED_1974 = date(1974, 7, 2)

WILAYAS = [
    "Adrar",
    "Chlef",
    "Laghouat",
    "Oum El Bouaghi",
    "Batna",
    "Béjaïa",
    "Biskra",
    "Béchar",
    "Blida",
    "Bouira",
    "Tamanrasset",
    "Tébessa",
    "Tlemcen",
    "Tiaret",
    "Tizi Ouzou",
    "Alger",
    "Djelfa",
    "Jijel",
    "Sétif",
    "Saïda",
    "Skikda",
    "Sidi Bel Abbès",
    "Annaba",
    "Guelma",
    "Constantine",
    "Médéa",
    "Mostaganem",
    "M'Sila",
    "Mascara",
    "Ouargla",
    "Oran",
    "El Bayadh",
    "Illizi",
    "Bordj Bou Arréridj",
    "Boumerdès",
    "El Tarf",
    "Tindouf",
    "Tissemsilt",
    "El Oued",
    "Khenchela",
    "Souk Ahras",
    "Tipaza",
    "Mila",
    "Aïn Defla",
    "Naâma",
    "Aïn Témouchent",
    "Ghardaïa",
    "Relizane",
]
# The 31 wilayas of 1974 to 1984 share the first 31 codes, and Chlef was then called El Asnam.
WILAYAS_1974 = ["El Asnam" if name == "Chlef" else name for name in WILAYAS[:31]]


class Figures(NamedTuple):
    registered: int | None = None
    cast: int | None = None
    valid: int | None = None
    invalid: int | None = None
    yes: int | None = None
    no: int | None = None


class Vote(NamedTuple):
    day: date
    issue: str  # the JORADP issue that prints the proclamation: year and number
    issued: date
    en: str
    fr: str
    question_en: str
    question_fr: str
    body: str
    stage: str
    scheme: str | None  # the wilaya scheme of the per-wilaya table, if the proclamation prints one
    wilayas: Sequence[tuple[int | None, ...]]
    national: Figures
    abroad: Figures | None
    territory: Figures | None
    method: str


COMMISSION = "Commission électorale nationale"
COUNCIL = "Conseil constitutionnel"
TABLE_NOTE = (
    "The proclamation prints the result for each wilaya and for citizens abroad, and the archive stores those as "
    "printed. It prints no breakdown below the wilaya in the files the archive holds. "
)

VOTES_LIST: list[Vote] = [
    Vote(
        date(1962, 7, 1),
        "1962001",
        date(1962, 7, 6),
        "1962 Algerian self-determination referendum",
        "Référendum d'autodétermination de 1962",
        "Do you want Algeria to become an independent state cooperating with France under the conditions defined by "
        "the declarations of 19 March 1962?",
        "Voulez-vous que l'Algérie devienne un Etat indépendant coopérant avec la France dans les conditions "
        "définies par les déclarations du 19 mars 1962 ?",
        "Commission Centrale de Contrôle du Référendum d'Autodétermination",
        "preliminary",
        None,
        [],
        Figures(6_549_736, 6_017_680, 5_992_115, 25_565, 5_975_581, 16_534),
        None,
        None,
        "Provisional results the Central Control Commission proclaimed on 3 July 1962 for the 15 départements, with no "
        "breakdown by département. Ballots cast are the 'votants', and the invalid count is the 'blancs ou nuls' the "
        "proclamation prints as one figure. The valid votes, 'suffrages exprimés', are the yes and no votes together.",
    ),
    Vote(
        date(1963, 9, 8),
        "1963064",
        date(1963, 9, 10),
        "1963 Algerian constitutional referendum",
        "Référendum constitutionnel de 1963",
        "Have you approved the Constitution that is proposed to you?",
        "Avez-vous approuvé la Constitution qui vous est proposée ?",
        "Commission nationale de Référendum constitutionnel",
        "certified",
        None,
        [],
        Figures(6_391_818, 5_283_974, 5_270_597, 13_377, 5_166_185, 105_047),
        None,
        None,
        "Definitive results the national constitutional referendum commission proclaimed on 10 September 1963, with no "
        "breakdown by département. The proclamation prints 5,270,597 valid votes, but its yes and no figures add up "
        "to 5,271,232; the archive stores every figure as printed.",
    ),
    Vote(
        date(1976, 6, 27),
        "1976061",
        date(1976, 7, 30),
        "1976 Algerian referendum on the National Charter",
        "Référendum sur la Charte nationale de 1976",
        "Referendum on the National Charter",
        "Référendum sur la Charte nationale",
        COMMISSION,
        "certified",
        "dz.wilaya_1974",
        tables.W1976_06,
        Figures(7_940_978, 7_290_671, None, None, 7_130_033, 118_508),
        Figures(None, None, None, None, 289_938, 14_779),
        None,
        TABLE_NOTE
        + "The minutes of 29 June 1976 give the wilayas' yes and no votes by the 31 wilayas of the time, and the votes "
        "abroad as one total after a list of embassies and consulates that the archive does not store. The wilaya "
        "figures add up to 6,890,095 yes against the 6,840,095 the minutes print for the national territory, and "
        "to 103,729 no, as printed. The minutes print the valid votes as a figure the scan does not render "
        "legibly, so none is stored.",
    ),
    Vote(
        date(1976, 11, 19),
        "1976094",
        date(1976, 11, 24),
        "1976 Algerian constitutional referendum",
        "Référendum sur la Constitution de 1976",
        "Referendum on the Constitution",
        "Référendum sur la Constitution",
        COMMISSION,
        "certified",
        "dz.wilaya_1974",
        tables.W1976_11,
        Figures(8_076_834, 7_504_696, 7_479_689, None, 7_407_626, None),
        Figures(None, None, None, None, 324_767, 10_105),
        None,
        TABLE_NOTE
        + "The minutes of 21 November 1976 give the yes and no votes by the 31 wilayas of the time, and the votes "
        "abroad as one total after a list of embassies and consulates that the archive does not store. The "
        "minutes' overall no figure is not legible in the scan and is not stored.",
    ),
    Vote(
        date(1986, 1, 16),
        "1986007",
        date(1986, 2, 16),
        "1986 Algerian referendum on the enrichment of the National Charter",
        "Référendum sur l'enrichissement de la Charte nationale de 1986",
        "Referendum on the enrichment of the National Charter",
        "Référendum sur l'enrichissement de la Charte nationale",
        COMMISSION,
        "certified",
        "dz.wilaya",
        tables.W1986,
        Figures(None, None, None, None, 10_508_863, 181_063),
        Figures(None, None, None, None, 249_929, 11_575),
        Figures(10_954_063, 10_502_524, 10_428_422, None, 10_258_934, 169_488),
        TABLE_NOTE
        + "The minutes of 17 January 1986 print the electorate, the voters, and the valid votes for the national "
        "territory only, which the archive holds on a separate unit; the votes abroad and the overall yes and no "
        "votes are printed apart. The wilayas add up to the territory's yes and no votes exactly.",
    ),
    Vote(
        date(1988, 11, 3),
        "1988045",
        date(1988, 11, 5),
        "1988 Algerian constitutional referendum",
        "Référendum sur la révision constitutionnelle de 1988",
        "Referendum on the draft constitutional revision",
        "Référendum sur le projet de révision constitutionnelle",
        COMMISSION,
        "certified",
        "dz.wilaya",
        tables.W1988,
        Figures(12_572_043, 10_435_046, 10_122_106, 312_940, 9_341_429, 780_677),
        Figures(None, None, None, None, 223_180, 22_786),
        None,
        TABLE_NOTE
        + "The minutes of 4 November 1988 print the polling stations (36,836), the electorate, the voters, the null "
        "ballots, and the valid votes for the whole electorate, including abroad. The wilaya yes votes add up to "
        "9,117,949 against the 9,118,249 the minutes print for the national territory; the no votes add up to the "
        "printed 757,891.",
    ),
    Vote(
        date(1989, 2, 23),
        "1989009",
        date(1989, 3, 1),
        "1989 Algerian constitutional referendum",
        "Référendum sur la révision constitutionnelle de 1989",
        "Referendum on the draft constitutional revision",
        "Référendum sur le projet de révision constitutionnelle",
        COMMISSION,
        "certified",
        "dz.wilaya",
        tables.W1989,
        Figures(13_170_137, 10_401_548, 9_928_438, 473_110, 7_290_760, 2_637_678),
        Figures(None, None, None, None, 188_014, 11_197),
        None,
        TABLE_NOTE
        + "The minutes of 24 February 1989 print the polling stations (39,037), the electorate, the voters, the null "
        "ballots, and the valid votes for the whole electorate, including abroad. The wilayas add up to the "
        "national territory's printed 7,102,746 yes and 2,626,481 no. The commission annulled the votes of 13 "
        "polling stations in the commune of Ras El Oued, wilaya of Bordj Bou Arréridj, and added them to the null "
        "ballots.",
    ),
    Vote(
        date(1996, 11, 28),
        "1996076",
        date(1996, 12, 8),
        "1996 Algerian constitutional referendum",
        "Référendum sur la révision constitutionnelle de 1996",
        "Referendum on the draft constitutional revision",
        "Référendum relatif au projet de révision constitutionnelle",
        COUNCIL,
        "certified",
        "dz.wilaya",
        tables.W1996,
        Figures(16_434_574, 13_111_514, 12_750_027, None, 10_785_919, 1_964_108),
        Figures(733_120, 311_508, 301_276, None, 271_555, 29_721),
        None,
        TABLE_NOTE
        + "The Constitutional Council's proclamation of 1 December 1996 prints each wilaya's electorate, voters, "
        "valid votes, and yes and no votes, with citizens abroad as the 49th row. The council set aside the "
        "results of the commune of Marhoum, wilaya of Sidi Bel Abbès, because the wilaya commission's minutes "
        "reached it too late. The wilayas and the votes abroad add up to the printed totals.",
    ),
    Vote(
        date(1999, 9, 16),
        "1999066",
        date(1999, 9, 21),
        "1999 Algerian referendum on civil concord",
        "Référendum de 1999 sur la concorde civile",
        "Referendum on the President of the Republic's overall approach to achieving peace and civil concord",
        "Référendum relatif à la démarche globale du Président de la République en vue de la réalisation de la paix "
        "et de la concorde civile",
        COUNCIL,
        "certified",
        "dz.wilaya",
        tables.W1999,
        Figures(17_512_726, 14_890_895, 14_785_571, 105_324, 14_583_075, 202_496),
        Figures(798_680, 491_855, 487_453, 4_402, 480_144, 7_309),
        None,
        TABLE_NOTE
        + "The Constitutional Council's proclamation of 19 September 1999 has an annex with the results of each "
        "wilaya and of citizens abroad, and then of each commune; the archive stores the wilaya level here. The "
        "wilayas and the votes abroad add up to the printed totals.",
    ),
    Vote(
        date(2005, 9, 29),
        "2005067",
        date(2005, 10, 5),
        "2005 Algerian referendum on the Charter for Peace and National Reconciliation",
        "Référendum de 2005 sur la Charte pour la paix et la réconciliation nationale",
        "Referendum on the Charter for Peace and National Reconciliation",
        "Référendum relatif à la Charte pour la paix et la réconciliation nationale",
        COUNCIL,
        "certified",
        "dz.wilaya",
        tables.W2005,
        Figures(18_313_594, 14_606_344, 14_435_113, 171_231, 14_057_371, 377_748),
        Figures(903_727, 324_524, 319_034, 5_490, 300_466, 18_568),
        None,
        TABLE_NOTE
        + "The Constitutional Council's proclamation of 1 October 2005 has an annex with the results of each wilaya "
        "and of citizens abroad, and then of each commune; the archive stores the wilaya level here. The annex "
        "prints the yes and no votes of Guelma and Mascara as one and five votes above their valid votes; the "
        "archive stores them as printed. The wilayas and the votes abroad add up to the printed totals.",
    ),
    Vote(
        date(2020, 11, 1),
        "2020072",
        date(2020, 12, 3),
        "2020 Algerian constitutional referendum",
        "Référendum sur la révision de la Constitution de 2020",
        "Referendum on the draft revision of the Constitution",
        "Référendum sur le projet de révision de la Constitution",
        COUNCIL,
        "certified",
        "dz.wilaya",
        tables.W2020,
        Figures(24_466_618, 5_661_551, 5_024_239, 637_312, 3_356_091, 1_668_148),
        Figures(907_298, 45_070, 42_009, 3_061, 32_776, 9_233),
        None,
        TABLE_NOTE
        + "The Constitutional Council's proclamation 01/P.CC/20 of 12 November 2020 prints an annex with the results "
        "of each of the 48 wilayas and of residents abroad. The proclamation's text gives 637,308 null ballots "
        "while its annex totals 637,312, which is what the wilayas and the votes abroad add up to; the annex "
        "figures are stored. Voters who cast a ballot abroad are 45,070 of the 907,298 registered there.",
    ),
]
ISSUES = {vote.issue: f"{BASE}{vote.issue[:4]}/F{vote.issue}.pdf" for vote in VOTES_LIST}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NATION = key("unit", "dz.nation", "DZ")
TERRITORY = key("unit", "dz.nation", "DZ-TERR")
ABROAD = key("unit", "dz.abroad", "ABR")
SYSTEM = key("electoral_system", "referendum")


def wilaya_unit(scheme: str, number: int) -> str:
    return key("unit", scheme, f"{number:02d}")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{vote.day}" for vote in VOTES_LIST)]


def fetch() -> None:
    for url in ISSUES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def check(vote: Vote) -> None:
    """Check each row's shape and report how the wilayas add up to the totals the proclamation prints."""
    figures = [Figures(*row) for row in vote.wilayas]
    for number, f in enumerate(figures, 1):
        if f.cast is not None and f.registered is not None and f.cast > f.registered:
            raise ValueError(f"{vote.day} wilaya {number}: more ballots than voters")
        if f.valid is not None and f.yes is not None and f.no is not None and f.yes + f.no > f.valid + 5:
            raise ValueError(f"{vote.day} wilaya {number}: yes and no votes exceed the valid votes")
    if vote.scheme is None:
        return
    if len(figures) != (31 if vote.scheme == "dz.wilaya_1974" else 48):
        raise ValueError(f"{vote.day}: {len(figures)} wilayas")
    printed = vote.territory or vote.national
    for name in Figures._fields:
        printed_value = getattr(printed, name)
        if printed_value is None or all(getattr(f, name) is None for f in figures):
            continue
        summed = sum(getattr(f, name) or 0 for f in figures)
        if vote.territory is None and vote.abroad is not None:
            summed += getattr(vote.abroad, name) or 0
        print(
            f"{vote.day} {name}: wilayas {summed:,}, printed {printed_value:,}, difference {summed - printed_value:,}"
        )


def parse() -> None:
    for vote in VOTES_LIST:
        check(vote)
    registry().write()
    for vote in VOTES_LIST:
        build(vote).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Algeria's national referendum units: the nation, the wilayas of 1974 and of 1984, and voters abroad",
    )
    p.external.update({ALGERIA, COUNTRY})
    # dz.wilaya matches the row the boundaries and presidential pipelines write for the same scheme.
    for code, rank, noun, system in (
        ("dz.nation", 1, "nation", None),
        ("dz.wilaya_1974", 2, "wilaya (1974)", None),
        ("dz.wilaya", 1, "wilaya", "wilaya_number"),
        ("dz.abroad", 2, "voters abroad", None),
    ):
        p.add("scheme", code=code, polity_id=ALGERIA, kind="administrative", rank=rank, noun=noun, code_system=system)
    p.add("electoral_system", id=SYSTEM, polity_id=ALGERIA, rules={"formula": "referendum"}, rules_version=1)
    p.name(SYSTEM, "Algerian referendum", "en", "official")
    p.add(
        "note",
        id=p.id("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="A vote on one question put to every registered voter, with a yes and a no on the ballot. A "
        "commission or the Constitutional Council proclaims the result, and the question carries if the yes votes "
        "are the majority of the valid votes.",
        lang="en",
    )

    def unit(
        id: str,
        scheme: str,
        code: str,
        kind: str,
        relation: str,
        area: str | None,
        parent: str | None,
        valid_from: date | None = None,
        valid_to: date | None = None,
    ) -> None:
        p.add(
            "reporting_unit",
            id=id,
            polity_id=ALGERIA,
            scheme_code=scheme,
            code=code,
            parent_id=parent,
            area_relation=relation,
            area_id=area,
            kind=kind,
            valid_from=valid_from,
            valid_to=valid_to,
        )

    unit(NATION, "dz.nation", "DZ", "aggregate", "equals", COUNTRY, None)
    p.name(NATION, "Algeria", "en", "official")
    unit(TERRITORY, "dz.nation", "DZ-TERR", "aggregate", "unlocated", None, NATION)
    p.name(TERRITORY, "Algeria, national territory (without voters abroad)", "en", "official")
    unit(ABROAD, "dz.abroad", "ABR", "abroad_mission", "unlocated", None, NATION)
    p.name(ABROAD, "Algerians abroad", "en", "official")
    for scheme, names, since, until in (
        ("dz.wilaya_1974", WILAYAS_1974, CREATED_1974, ADDED_1984),
        ("dz.wilaya", WILAYAS, ADDED_1984, None),
    ):
        for number, name in enumerate(names, 1):
            id = wilaya_unit(scheme, number)
            unit(id, scheme, f"{number:02d}", "standard", "unlocated", None, NATION, since, until)
            p.name(id, name, "fr", "official")
    return p


def build(vote: Vote) -> Package:
    p = Package(
        f"{SOURCE}/{vote.day}",
        SOURCE,
        f"{vote.en}, from the proclamation in the Journal officiel",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({ALGERIA, COUNTRY})
    url = ISSUES[vote.issue]
    source = p.id("source", vote.day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=f"Journal officiel de la République algérienne n° {int(vote.issue[4:])} of {vote.issued}: "
        f"proclamation of the results of the referendum of {vote.day}",
        url=url,
        license=LICENSE,
        attribution="Secrétariat général du Gouvernement, Algeria",
    )
    p.artifact(raw.cached(SOURCE, url), source)

    election = p.id("election", vote.day)
    p.add(
        "election",
        id=election,
        polity_id=ALGERIA,
        kind="referendum",
        date_from=vote.day,
        date_to=vote.day,
        date_precision="day",
        status="held",
    )
    p.name(election, vote.en, "en", "official")
    p.name(election, vote.fr, "fr", "official", False)
    base = f"dz/{vote.day}-referendum"
    p.add("slug", path=base, entity_id=election, is_current=True)

    contest = p.id("contest", vote.day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        electoral_system_id=SYSTEM,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, vote.question_en, "en", "official")
    p.name(contest, vote.question_fr, "fr", "official", False)
    p.add("slug", path=f"{base}/question", entity_id=contest, is_current=True)
    options = []
    for position, (label, english) in enumerate((("OUI", "Yes"), ("NON", "No")), 1):
        option = p.id("option", vote.day, position)
        options.append(option)
        p.add(
            "contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=label
        )
        p.name(option, english, "en", "english")

    result_set = p.id("result_set", vote.day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage=vote.stage,
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=p.id("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=f"Proclaimed by the {vote.body} and printed in the Journal officiel of {vote.issued}. {vote.method}",
        lang="en",
    )
    p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won")

    rows: list[tuple[str, str | None, int, int]] = []

    def add(unit: str, f: Figures) -> None:
        for measure, value in (
            (REGISTERED, f.registered),
            (CAST, f.cast),
            (VALID, f.valid),
            (INVALID, f.invalid),
        ):
            if value is not None:
                rows.append((unit, None, measure, value))
        for option, value in ((options[0], f.yes), (options[1], f.no)):
            if value is not None:
                rows.append((unit, option, VOTES, value))

    add(NATION, vote.national)
    if vote.abroad is not None:
        add(ABROAD, vote.abroad)
    if vote.territory is not None:
        add(TERRITORY, vote.territory)
    if vote.scheme is not None:
        for number, row in enumerate(vote.wilayas, 1):
            add(wilaya_unit(vote.scheme, number), Figures(*row))
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
